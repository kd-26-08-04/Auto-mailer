"""Regression tests for sequence upgrade: enroll-on-running, no duplicate,
delete, attachments, open tracking, exact delay interval."""

import json
import os
import sys
import unittest
from datetime import datetime, timedelta
from pathlib import Path
from unittest.mock import patch, MagicMock

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from bson.objectid import ObjectId

from sequence_engine import (
    _compute_next_send_after_step,
    _normalize_settings,
    activate_sequence,
    create_sequence,
    delete_sequence,
    delete_step_attachment,
    enroll_from_data,
    get_sequence,
    list_sequences,
    pause_sequence,
    process_due_sends,
    record_email_open,
    resume_sequence,
    store_step_attachment,
    update_sequence,
)
from auto_mailer_engine import get_db, get_mongo_client

TEST_USER = "test-upgrade-" + datetime.now().strftime("%H%M%S%f")


def mongo_available() -> bool:
    try:
        get_mongo_client().admin.command("ping")
        return True
    except Exception:
        return False


def skip_without_mongo():
    return unittest.skipUnless(mongo_available(), "MongoDB not available")


class DelaySettingsTests(unittest.TestCase):
    def test_normalize_settings_uses_exact_delay_no_jitter(self):
        s = _normalize_settings({"delay_sec": 45, "daily_limit": 10})
        self.assertEqual(s["delay_sec"], 45)
        self.assertEqual(s["min_delay_sec"], 45)
        self.assertEqual(s["max_delay_sec"], 45)

    def test_step_delay_is_deterministic(self):
        settings = _normalize_settings({"delay_sec": 30, "window_start": "00:00", "window_end": "23:59"})
        steps = [
            {"step_index": 0, "delay_days": 0, "delay_hours": 0},
            {"step_index": 1, "delay_days": 2, "delay_hours": 0},
        ]
        base = datetime(2026, 1, 1, 10, 0, 0)
        next1 = _compute_next_send_after_step(settings, steps, 1, base)
        next2 = _compute_next_send_after_step(settings, steps, 1, base)
        self.assertEqual(next1, next2)
        self.assertEqual(next1, base + timedelta(days=2))


@skip_without_mongo()
class SequenceUpgradeRegressionTests(unittest.TestCase):
    user_id: str = ""

    @classmethod
    def setUpClass(cls):
        db = get_db()
        res = db.users.insert_one({
            "username": TEST_USER,
            "password_hash": "test",
            "smtp_email": "upgrade@example.com",
            "smtp_app_password": "app-pw",
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
        db.sequence_attachments.delete_many({"user_id": cls.user_id})

    def setUp(self):
        db = get_db()
        db.sequences.delete_many({"user_id": self.user_id})
        db.enrollments.delete_many({"user_id": self.user_id})
        db.sequence_send_log.delete_many({"user_id": self.user_id})
        db.sequence_attachments.delete_many({"user_id": self.user_id})
        db.recipients.delete_many({"user_id": self.user_id})

    def _make_seq(self, name="Upgrade Seq"):
        return create_sequence(
            self.user_id,
            name,
            steps=[
                {"subject": "Email 1", "body": "<p>One</p>", "delay_days": 0},
                {"subject": "Email 2", "body": "<p>Two</p>", "delay_days": 2},
                {"subject": "Email 3", "body": "<p>Three</p>", "delay_days": 3},
            ],
            settings={
                "daily_limit": 50,
                "delay_sec": 5,
                "consent_required": False,
                "window_start": "00:00",
                "window_end": "23:59",
                "enable_reply_tracking": False,
            },
        )

    def test_activate_does_not_duplicate_sequence(self):
        seq = self._make_seq("No Dup")
        enroll_from_data(self.user_id, seq["id"], [{"email": "a@example.com", "consent": "true"}])
        activate_sequence(self.user_id, seq["id"])
        listed = list_sequences(self.user_id)
        matching = [s for s in listed if s["name"] == "No Dup"]
        self.assertEqual(len(matching), 1)
        self.assertEqual(matching[0]["status"], "active")
        self.assertEqual(matching[0]["id"], seq["id"])

    def test_enroll_on_running_starts_at_email_1(self):
        seq = self._make_seq("Enroll Running")
        enroll_from_data(self.user_id, seq["id"], [{"email": "existing@example.com", "consent": "true"}])
        activate_sequence(self.user_id, seq["id"])

        db = get_db()
        # Simulate existing contact already on step 2
        db.enrollments.update_one(
            {"sequence_id": ObjectId(seq["id"]), "email": "existing@example.com"},
            {"$set": {"current_step": 2, "last_sent_at": datetime.now().isoformat()}},
        )

        pause_sequence(self.user_id, seq["id"])
        enrolled = enroll_from_data(
            self.user_id,
            seq["id"],
            [{"email": "new@example.com", "first_name": "New", "consent": "true"}],
        )
        self.assertEqual(enrolled, 1)

        existing = db.enrollments.find_one({"sequence_id": ObjectId(seq["id"]), "email": "existing@example.com"})
        newbie = db.enrollments.find_one({"sequence_id": ObjectId(seq["id"]), "email": "new@example.com"})
        self.assertEqual(existing["current_step"], 2)
        self.assertEqual(newbie["current_step"], 0)
        self.assertEqual(newbie["status"], "active")

        resume_sequence(self.user_id, seq["id"])
        existing2 = db.enrollments.find_one({"_id": existing["_id"]})
        self.assertEqual(existing2["current_step"], 2)

    def test_update_requires_pause_when_active(self):
        seq = self._make_seq("Edit Guard")
        enroll_from_data(self.user_id, seq["id"], [{"email": "e@example.com", "consent": "true"}])
        activate_sequence(self.user_id, seq["id"])
        with self.assertRaises(ValueError):
            update_sequence(self.user_id, seq["id"], name="Should Fail")
        pause_sequence(self.user_id, seq["id"])
        updated = update_sequence(self.user_id, seq["id"], name="Renamed OK")
        self.assertEqual(updated["name"], "Renamed OK")

    def test_soft_delete_hides_and_blocks_sends(self):
        seq = self._make_seq("Delete Me")
        enroll_from_data(self.user_id, seq["id"], [{"email": "d@example.com", "consent": "true"}])
        activate_sequence(self.user_id, seq["id"])
        delete_sequence(self.user_id, seq["id"])
        delete_sequence(self.user_id, seq["id"])  # idempotent

        listed = list_sequences(self.user_id)
        self.assertFalse(any(s["id"] == seq["id"] for s in listed))

        db = get_db()
        doc = db.sequences.find_one({"_id": ObjectId(seq["id"])})
        self.assertEqual(doc["status"], "deleted")

        with patch("sequence_engine._smtp_send") as mock_send:
            result = process_due_sends(tracking_base_url="http://localhost", max_per_run=10, sync_sleep=False)
            self.assertEqual(mock_send.call_count, 0)
            self.assertEqual(result["sent"], 0)

    def test_attachment_persisted_and_sent(self):
        seq = self._make_seq("Attach")
        meta = store_step_attachment(
            self.user_id,
            seq["id"],
            0,
            "hello.txt",
            "text/plain",
            b"hello attachment",
        )
        self.assertEqual(meta["filename"], "hello.txt")
        refreshed = get_sequence(self.user_id, seq["id"])
        self.assertIn(meta["id"], refreshed["steps"][0].get("attachment_ids", []))

        enroll_from_data(self.user_id, seq["id"], [{"email": "att@example.com", "consent": "true"}])
        activate_sequence(self.user_id, seq["id"])
        db = get_db()
        db.enrollments.update_many(
            {"sequence_id": ObjectId(seq["id"])},
            {"$set": {"next_send_at": (datetime.now() - timedelta(minutes=1)).isoformat(timespec="seconds")}},
        )

        with patch("sequence_engine._smtp_send") as mock_send:
            process_due_sends(tracking_base_url="http://localhost:5001", max_per_run=5, sync_sleep=False)
            self.assertGreater(mock_send.call_count, 0)
            args, kwargs = mock_send.call_args
            mem = kwargs.get("memory_attachments") or []
            self.assertTrue(any(m[0] == "hello.txt" and m[1] == b"hello attachment" for m in mem))

        if get_sequence(self.user_id, seq["id"])["status"] == "active":
            pause_sequence(self.user_id, seq["id"])
        delete_step_attachment(self.user_id, seq["id"], meta["id"])
        refreshed2 = get_sequence(self.user_id, seq["id"])
        self.assertNotIn(meta["id"], refreshed2["steps"][0].get("attachment_ids", []))

    def test_open_tracking_by_send_log_id(self):
        seq = self._make_seq("Track")
        enroll_from_data(self.user_id, seq["id"], [{"email": "o@example.com", "consent": "true"}])
        activate_sequence(self.user_id, seq["id"])
        db = get_db()
        enr = db.enrollments.find_one({"sequence_id": ObjectId(seq["id"])})
        res = db.sequence_send_log.insert_one({
            "user_id": self.user_id,
            "sequence_id": ObjectId(seq["id"]),
            "enrollment_id": enr["_id"],
            "recipient_id": enr["recipient_id"],
            "email": "o@example.com",
            "step_index": 0,
            "day_key": "2026-01-01",
            "status": "sent",
            "opened": 0,
            "open_count": 0,
            "sent_at": datetime.now().isoformat(),
        })
        log_id = str(res.inserted_id)
        first = record_email_open(log_id)
        self.assertTrue(first["first_open"])
        second = record_email_open(log_id)
        self.assertFalse(second["first_open"])
        log = db.sequence_send_log.find_one({"_id": res.inserted_id})
        self.assertEqual(log["opened"], 1)
        self.assertEqual(log["open_count"], 2)
        self.assertIsNotNone(log.get("opened_at"))


@skip_without_mongo()
class FlaskUpgradeAPITests(unittest.TestCase):
    user_id: str = ""

    @classmethod
    def setUpClass(cls):
        os.environ["ENABLE_INLINE_WORKER"] = "0"
        from web_app import app
        cls.app = app
        cls.client = app.test_client()
        db = get_db()
        res = db.users.insert_one({
            "username": TEST_USER + "-api2",
            "password_hash": "test",
            "smtp_email": "api2@example.com",
            "smtp_app_password": "pw",
            "created_at": datetime.utcnow(),
        })
        cls.user_id = str(res.inserted_id)

    @classmethod
    def tearDownClass(cls):
        db = get_db()
        db.users.delete_one({"_id": ObjectId(cls.user_id)})
        db.sequences.delete_many({"user_id": cls.user_id})
        db.enrollments.delete_many({"user_id": cls.user_id})
        db.sequence_attachments.delete_many({"user_id": cls.user_id})

    def setUp(self):
        with self.client.session_transaction() as sess:
            sess["user_id"] = self.user_id
            sess["username"] = "api2"
        get_db().sequences.delete_many({"user_id": self.user_id})

    def test_api_delete_and_list(self):
        payload = {
            "name": "API Delete",
            "steps": [{"subject": "S", "body": "<p>x</p>", "delay_days": 0}],
            "settings": {"delay_sec": 3, "consent_required": False},
        }
        res = self.client.post("/api/sequences", data=json.dumps(payload), content_type="application/json")
        seq_id = res.get_json()["sequence"]["id"]
        del_res = self.client.delete(f"/api/sequences/{seq_id}")
        self.assertEqual(del_res.status_code, 200)
        listed = self.client.get("/api/sequences").get_json()["sequences"]
        self.assertFalse(any(s["id"] == seq_id for s in listed))

    def test_track_open_log_endpoint(self):
        db = get_db()
        log_id = db.sequence_send_log.insert_one({
            "user_id": self.user_id,
            "sequence_id": ObjectId(),
            "enrollment_id": ObjectId(),
            "recipient_id": ObjectId(),
            "email": "t@example.com",
            "step_index": 0,
            "day_key": "2026-01-02",
            "status": "sent",
            "opened": 0,
            "open_count": 0,
        }).inserted_id
        res = self.client.get(f"/track/open/log/{log_id}")
        self.assertEqual(res.status_code, 200)
        self.assertEqual(res.content_type, "image/gif")
        doc = db.sequence_send_log.find_one({"_id": log_id})
        self.assertEqual(doc["opened"], 1)


if __name__ == "__main__":
    unittest.main(verbosity=2)
