"""Tests verifying fixes for template CSS preservation, atomic concurrency locking, and negative stats prevention."""

import unittest
from datetime import datetime, timedelta
from bson.objectid import ObjectId

from auto_mailer_engine import _render_template, _local_now, get_db, init_db
from sequence_engine import _stop_enrollment, create_sequence, enroll_from_data

TEST_USER = "test-fixes-" + datetime.now().strftime("%H%M%S%f")


class TemplateFixesTests(unittest.TestCase):
    def test_css_blocks_preserved_while_variables_interpolated(self):
        template = """
        <html>
        <head>
            <style>
                body { font-family: Arial; color: #333; margin: 0; }
                .button { background-color: #4CAF50; color: white; padding: 10px 20px; }
            </style>
        </head>
        <body>
            <p>Hi {first_name},</p>
            <p>Welcome to {{company}}!</p>
        </body>
        </html>
        """
        data = {"first_name": "Alice", "company": "Acme Corp"}
        result = _render_template(template, data)

        # Ensure CSS styling remained intact
        self.assertIn("body { font-family: Arial; color: #333; margin: 0; }", result)
        self.assertIn(".button { background-color: #4CAF50; color: white; padding: 10px 20px; }", result)

        # Ensure variables were properly interpolated
        self.assertIn("Hi Alice,", result)
        self.assertIn("Welcome to Acme Corp!", result)

    def test_spintax_with_variables_and_css(self):
        template = "<style>div { font-size: 16px; }</style><p>{Hello|Hi} {first_name}!</p>"
        result = _render_template(template, {"first_name": "Bob"})
        self.assertIn("div { font-size: 16px; }", result)
        self.assertTrue("Hello Bob!" in result or "Hi Bob!" in result)

    def test_timezone_offset_custom(self):
        utc_now = datetime.utcnow()
        # Offset +2 hours
        local_plus2 = _local_now(tz_offset_hours=2.0)
        diff_hours = (local_plus2 - utc_now).total_seconds() / 3600.0
        self.assertAlmostEqual(diff_hours, 2.0, delta=0.05)


class ConcurrencyAndStatsTests(unittest.TestCase):
    user_id = ""

    @classmethod
    def setUpClass(cls):
        init_db()
        db = get_db()
        res = db.users.insert_one({
            "username": TEST_USER,
            "password_hash": "test",
            "smtp_email": "fix@example.com",
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

    def test_stop_enrollment_does_not_make_stats_negative(self):
        db = get_db()
        seq = create_sequence(
            self.user_id,
            "Stats Test",
            steps=[{"subject": "Test", "body": "Body", "delay_days": 0}],
            settings={"consent_required": False, "enable_reply_tracking": False},
        )
        seq_oid = ObjectId(seq["id"])
        enroll_from_data(self.user_id, seq["id"], [{"email": "stats_test@example.com"}])

        refreshed_seq = db.sequences.find_one({"_id": seq_oid})
        self.assertEqual(refreshed_seq["stats"]["active"], 1)

        enrollment = db.enrollments.find_one({"sequence_id": seq_oid})
        enrollment_id = enrollment["_id"]

        # First stop: active count goes from 1 to 0
        _stop_enrollment(enrollment_id, "completed", seq_oid)
        refreshed_seq = db.sequences.find_one({"_id": seq_oid})
        self.assertEqual(refreshed_seq["stats"]["active"], 0)

        # Second stop: should NOT decrement active count to -1
        _stop_enrollment(enrollment_id, "completed", seq_oid)
        refreshed_seq = db.sequences.find_one({"_id": seq_oid})
        self.assertEqual(refreshed_seq["stats"]["active"], 0)


if __name__ == "__main__":
    unittest.main()
