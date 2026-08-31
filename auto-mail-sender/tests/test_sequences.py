"""Tests for sequence engine, API, and A/B subject variants."""

import json
import os
import sys
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch

# Ensure project root is on path
ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from bson.objectid import ObjectId

from sequence_engine import (
    _normalize_steps,
    _pick_subject_variant,
    _render_step_subject,
    activate_sequence,
    create_sequence,
    enroll_from_csv,
    get_sequence,
    get_sequence_dashboard,
    list_sequences,
    pause_sequence,
    preview_sequence_steps,
    process_due_sends,
    resume_sequence,
    update_sequence,
)
from auto_mailer_engine import get_db, get_mongo_client


TEST_USER = "test-seq-user-" + datetime.now().strftime("%H%M%S%f")
CSV_FIXTURE = str(ROOT / "example_recipients.csv")


def mongo_available() -> bool:
    try:
        client = get_mongo_client()
        client.admin.command("ping")
        return True
    except Exception:
        return False


def skip_without_mongo():
    return unittest.skipUnless(mongo_available(), "MongoDB not available")


class SequenceLogicTests(unittest.TestCase):
    """Pure logic tests — no MongoDB required."""

    def test_normalize_steps_with_ab_variants(self):
        steps = _normalize_steps([
            {
                "subject": "Primary",
                "subject_variants": ["Variant A {name}", "Variant B {name}"],
                "body": "<p>Hi</p>",
                "delay_days": 0,
            },
            {
                "subject": "Follow up",
                "body": "<p>Follow</p>",
                "delay_days": 3,
            },
        ])
        self.assertEqual(len(steps), 2)
        self.assertEqual(steps[0]["subject_variants"], ["Variant A {name}", "Variant B {name}"])
        self.assertEqual(steps[1]["subject_variants"], ["Follow up"])

    def test_normalize_steps_requires_subject(self):
        with self.assertRaises(ValueError):
            _normalize_steps([{"body": "<p>x</p>", "delay_days": 0}])

    def test_pick_subject_variant_deterministic(self):
        step = {"subject_variants": ["A", "B", "C"]}
        a = _pick_subject_variant(step, "enroll-123", 0)
        b = _pick_subject_variant(step, "enroll-123", 0)
        self.assertEqual(a, b)
        self.assertIn(a, ["A", "B", "C"])

    def test_pick_subject_variant_distributes(self):
        step = {"subject_variants": ["A", "B"]}
        picks = {_pick_subject_variant(step, f"id-{i}", 0) for i in range(20)}
        self.assertTrue(len(picks) >= 2, "Expected multiple variants across enrollments")

    def test_render_step_subject_interpolation(self):
        step = {"subject_variants": ["Hello {first_name}"]}
        subj = _render_step_subject(step, {"first_name": "Jane"}, "e1", 0)
        self.assertEqual(subj, "Hello Jane")


@skip_without_mongo()
class SequenceIntegrationTests(unittest.TestCase):
    user_id: str = ""

    @classmethod
    def setUpClass(cls):
        db = get_db()
        res = db.users.insert_one({
            "username": TEST_USER,
            "password_hash": "test",
            "smtp_email": "test@example.com",
            "smtp_app_password": "test-app-password",
            "created_at": datetime.utcnow(),
        })
        cls.user_id = str(res.inserted_id)

    @classmethod
    def tearDownClass(cls):
        db = get_db()
        db.users.delete_one({"_id": ObjectId(cls.user_id)})
        db.sequences.delete_many({"user_id": cls.user_id})
        db.enrollments.delete_many({"user_id": cls.user_id})
        db.recipients.delete_many({"user_id": cls.user_id})
        db.sequence_send_log.delete_many({"user_id": cls.user_id})

    def setUp(self):
        get_db().sequences.delete_many({"user_id": self.user_id})
        get_db().enrollments.delete_many({"user_id": self.user_id})

    def test_create_update_list_sequence(self):
        seq = create_sequence(
            self.user_id,
            "Test Sequence",
            steps=[
                {
                    "subject_variants": ["Hi {first_name}", "Hello {first_name}"],
                    "body": "<p>Body 1</p>",
                    "delay_days": 0,
                },
                {
                    "subject": "Follow up",
                    "body": "<p>Body 2</p>",
                    "delay_days": 2,
                },
            ],
            settings={"daily_limit": 50, "delay_sec": 1, "consent_required": False},
        )
        self.assertEqual(seq["status"], "draft")
        self.assertEqual(len(seq["steps"]), 2)
        self.assertEqual(len(seq["steps"][0]["subject_variants"]), 2)

        updated = update_sequence(
            self.user_id,
            seq["id"],
            name="Renamed Sequence",
            steps=[
                {"subject": "Step A", "body": "<p>A</p>", "delay_days": 0},
                {"subject": "Step B", "body": "<p>B</p>", "delay_days": 1},
                {"subject": "Step C", "body": "<p>C</p>", "delay_days": 3},
            ],
        )
        self.assertEqual(updated["name"], "Renamed Sequence")
        self.assertEqual(len(updated["steps"]), 3)

        all_seqs = list_sequences(self.user_id)
        self.assertTrue(any(s["id"] == seq["id"] for s in all_seqs))

    def test_enroll_and_activate(self):
        seq = create_sequence(
            self.user_id,
            "Enroll Test",
            steps=[{"subject": "Hi", "body": "<p>Hi</p>", "delay_days": 0}],
            settings={"consent_required": True},
        )
        enrolled = enroll_from_csv(self.user_id, seq["id"], CSV_FIXTURE)
        self.assertGreater(enrolled, 0)

        result = activate_sequence(self.user_id, seq["id"])
        self.assertEqual(result["status"], "active")
        active = get_sequence(self.user_id, seq["id"])
        self.assertEqual(active["status"], "active")

    def test_pause_resume(self):
        seq = create_sequence(
            self.user_id,
            "Pause Test",
            steps=[{"subject": "Hi", "body": "<p>Hi</p>", "delay_days": 0}],
        )
        enroll_from_csv(self.user_id, seq["id"], CSV_FIXTURE)
        activate_sequence(self.user_id, seq["id"])

        pause_sequence(self.user_id, seq["id"])
        self.assertEqual(get_sequence(self.user_id, seq["id"])["status"], "paused")

        resume_sequence(self.user_id, seq["id"])
        self.assertEqual(get_sequence(self.user_id, seq["id"])["status"], "active")

    def test_preview_with_variants(self):
        steps = [
            {
                "subject_variants": ["A {first_name}", "B {first_name}"],
                "body": "<p>{first_name}</p>",
                "delay_days": 0,
            }
        ]
        previews = preview_sequence_steps(self.user_id, steps, CSV_FIXTURE, limit=2)
        self.assertGreater(len(previews), 0)
        self.assertIn("subject_variant", previews[0])
        combined = (previews[0]["subject"] + previews[0]["body"]).lower()
        self.assertTrue("john" in combined or "sam" in combined, f"Expected interpolated name in preview: {combined}")

    @patch("sequence_engine._smtp_send")
    def test_process_due_sends_mock(self, mock_send):
        seq = create_sequence(
            self.user_id,
            "Worker Test",
            steps=[
                {
                    "subject_variants": ["Mail A {first_name}", "Mail B {first_name}"],
                    "body": "<p>Test {first_name}</p>",
                    "delay_days": 0,
                }
            ],
            settings={
                "daily_limit": 10,
                "delay_sec": 1,
                "consent_required": False,
                "window_start": "00:00",
                "window_end": "23:59",
                "enable_reply_tracking": False,
            },
        )
        enroll_from_csv(self.user_id, seq["id"], CSV_FIXTURE)
        activate_sequence(self.user_id, seq["id"])

        db = get_db()
        db.enrollments.update_many(
            {"sequence_id": ObjectId(seq["id"])},
            {"$set": {"next_send_at": (datetime.now() - timedelta(minutes=1)).isoformat(timespec="seconds")}},
        )

        result = process_due_sends(tracking_base_url="http://localhost:5001", max_per_run=10)
        self.assertGreater(result["sent"], 0)
        self.assertGreater(mock_send.call_count, 0)

        dashboard = get_sequence_dashboard(self.user_id, seq["id"])
        self.assertGreaterEqual(dashboard["stats"]["sent_today"], 1)


@skip_without_mongo()
class FlaskAPITests(unittest.TestCase):
    user_id: str = ""

    @classmethod
    def setUpClass(cls):
        os.environ["ENABLE_INLINE_WORKER"] = "0"
        from web_app import app
        cls.app = app
        cls.client = app.test_client()

        db = get_db()
        res = db.users.insert_one({
            "username": TEST_USER + "-api",
            "password_hash": "test",
            "smtp_email": "api@example.com",
            "smtp_app_password": "pw",
            "full_name": "API Tester",
            "created_at": datetime.utcnow(),
        })
        cls.user_id = str(res.inserted_id)

    @classmethod
    def tearDownClass(cls):
        db = get_db()
        db.users.delete_one({"_id": ObjectId(cls.user_id)})
        db.sequences.delete_many({"user_id": cls.user_id})

    def setUp(self):
        with self.client.session_transaction() as sess:
            sess["user_id"] = self.user_id
            sess["username"] = "api-tester"

    def test_api_create_and_get_sequence(self):
        payload = {
            "name": "API Sequence",
            "steps": [
                {
                    "subject_variants": ["Subj A", "Subj B"],
                    "body": "<p>Hello</p>",
                    "delay_days": 0,
                }
            ],
            "settings": {"daily_limit": 20, "delay_sec": 5},
        }
        res = self.client.post(
            "/api/sequences",
            data=json.dumps(payload),
            content_type="application/json",
        )
        self.assertEqual(res.status_code, 200)
        data = res.get_json()
        self.assertTrue(data["success"])
        seq_id = data["sequence"]["id"]

        res2 = self.client.get(f"/api/sequences/{seq_id}")
        self.assertEqual(res2.status_code, 200)
        seq = res2.get_json()["sequence"]
        self.assertEqual(len(seq["steps"][0]["subject_variants"]), 2)

    def test_api_list_sequences(self):
        res = self.client.get("/api/sequences")
        self.assertEqual(res.status_code, 200)
        self.assertTrue(res.get_json()["success"])

    def test_api_dashboard(self):
        res = self.client.get("/api/sequences/dashboard")
        self.assertEqual(res.status_code, 200)
        self.assertTrue(res.get_json()["success"])

    def test_index_page_loads(self):
        res = self.client.get("/")
        self.assertEqual(res.status_code, 200)
        self.assertIn(b"Sequences", res.data)


def run_tests():
    loader = unittest.TestLoader()
    suite = unittest.TestSuite()
    suite.addTests(loader.loadTestsFromTestCase(SequenceLogicTests))

    if mongo_available():
        suite.addTests(loader.loadTestsFromTestCase(SequenceIntegrationTests))
        suite.addTests(loader.loadTestsFromTestCase(FlaskAPITests))
        print("MongoDB connected — running full test suite")
    else:
        print("WARNING: MongoDB not available — skipping integration/API tests")

    runner = unittest.TextTestRunner(verbosity=2)
    result = runner.run(suite)
    return result.wasSuccessful()


if __name__ == "__main__":
    ok = run_tests()
    sys.exit(0 if ok else 1)
