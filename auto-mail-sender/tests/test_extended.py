"""Extended tests for inbox, analytics, and templates."""

import sys
from datetime import datetime
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import unittest
from bson.objectid import ObjectId

from apollo_templates import get_apollo_template, list_apollo_templates, save_template_override, SEQUENCES_DIR
from auto_mailer_engine import get_db, get_mongo_client
from sequence_engine import (
    activate_sequence,
    create_sequence,
    enroll_from_csv,
    get_analytics,
    get_inbox_activity,
)

TEST_USER = "uitest-ext-" + datetime.now().strftime("%H%M%S%f")
CSV_FIXTURE = str(ROOT / "example_recipients.csv")


def mongo_available():
    try:
        get_mongo_client().admin.command("ping")
        return True
    except Exception:
        return False


@unittest.skipUnless(mongo_available(), "MongoDB not available")
class ExtendedAPITests(unittest.TestCase):
    user_id = ""

    @classmethod
    def setUpClass(cls):
        import os
        os.environ["ENABLE_INLINE_WORKER"] = "0"
        db = get_db()
        res = db.users.insert_one({
            "username": TEST_USER,
            "password_hash": "test",
            "smtp_email": "ext@example.com",
            "smtp_app_password": "pw",
            "created_at": datetime.utcnow(),
        })
        cls.user_id = str(res.inserted_id)
        from web_app import app
        cls.app = app
        cls.client = app.test_client()

    @classmethod
    def tearDownClass(cls):
        db = get_db()
        db.users.delete_one({"_id": ObjectId(cls.user_id)})
        db.sequences.delete_many({"user_id": cls.user_id})
        db.enrollments.delete_many({"user_id": cls.user_id})
        db.template_overrides.delete_many({"user_id": cls.user_id})

    def setUp(self):
        with self.client.session_transaction() as sess:
            sess["user_id"] = self.user_id

    def test_bundled_templates_exist(self):
        self.assertTrue(SEQUENCES_DIR.is_dir())
        templates = list_apollo_templates()
        self.assertGreaterEqual(len(templates), 10)

    def test_template_load_and_save(self):
        tpl = get_apollo_template("healthy-food")
        self.assertEqual(tpl["step_count"], 5)
        saved = save_template_override(
            self.user_id, "healthy-food", "Custom Healthy Food",
            steps=tpl["steps"], settings=tpl["settings"],
        )
        self.assertEqual(saved["source"], "customized")
        listed = list_apollo_templates(self.user_id)
        self.assertTrue(any(t["id"] == "healthy-food" and t["customized"] for t in listed))

    def test_inbox_and_analytics_endpoints(self):
        seq = create_sequence(
            self.user_id, "Inbox Analytics Test",
            steps=[{"subject": "Hi {first_name}", "body": "<p>Hi</p>", "delay_days": 0}],
            settings={"consent_required": False, "enable_reply_tracking": False},
        )
        enroll_from_csv(self.user_id, seq["id"], CSV_FIXTURE)
        activate_sequence(self.user_id, seq["id"])

        with patch("sequence_engine._smtp_send"):
            from sequence_engine import process_due_sends
            from datetime import timedelta
            db = get_db()
            db.enrollments.update_many(
                {"sequence_id": ObjectId(seq["id"])},
                {"$set": {"next_send_at": (datetime.now() - timedelta(minutes=1)).isoformat(timespec="seconds")}},
            )
            process_due_sends(tracking_base_url="http://localhost:5001", max_per_run=5)

        inbox = get_inbox_activity(self.user_id, seq["id"])
        self.assertIn("groups", inbox)
        self.assertIn("group_lists", inbox)
        self.assertGreater(inbox["groups"].get("all_sent", 0), 0)

        analytics = get_analytics(self.user_id, seq["id"])
        self.assertIn("summary", analytics)
        self.assertIn("funnel", analytics)
        self.assertIn("steps", analytics)
        self.assertGreater(analytics["summary"]["total_emails_sent"], 0)

        r1 = self.client.get("/api/inbox")
        self.assertEqual(r1.status_code, 200)
        self.assertTrue(r1.get_json()["success"])

        r2 = self.client.get("/api/analytics")
        self.assertEqual(r2.status_code, 200)
        self.assertTrue(r2.get_json()["success"])

        r3 = self.client.get(f"/api/sequences/{seq['id']}/inbox")
        self.assertEqual(r3.status_code, 200)

        r4 = self.client.get(f"/api/sequences/{seq['id']}/analytics")
        self.assertEqual(r4.status_code, 200)

    def test_index_has_inbox_and_analytics(self):
        res = self.client.get("/")
        self.assertEqual(res.status_code, 200)
        self.assertIn(b"view-inbox", res.data)
        self.assertIn(b"view-analytics", res.data)
        self.assertIn(b"Inbox", res.data)
        self.assertIn(b"Analytics", res.data)


class ApolloParserTests(unittest.TestCase):
    def test_all_bundled_templates_parse(self):
        if not SEQUENCES_DIR.is_dir():
            self.skipTest("sequences dir missing")
        for path in SEQUENCES_DIR.glob("*.md"):
            if path.name.lower() == "readme.md":
                continue
            tpl = get_apollo_template(path.stem)
            self.assertGreaterEqual(tpl["step_count"], 1, path.name)


if __name__ == "__main__":
    unittest.main(verbosity=2)
