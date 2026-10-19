import unittest
from datetime import datetime, timezone

from flask import flash, render_template

from app import create_app
from app.timezones import format_nairobi_datetime


class UiFeedbackConfig:
    APP_ENV = "testing"
    SECRET_KEY = "test-secret"
    SQLALCHEMY_DATABASE_URI = "sqlite://"
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    SQLALCHEMY_ENGINE_OPTIONS = {}
    AUTO_CREATE_DB = False
    SCHEDULER_ENABLED = False
    WTF_CSRF_ENABLED = False


class UiFeedbackAndTimezoneTests(unittest.TestCase):
    def setUp(self):
        self.app = create_app(UiFeedbackConfig)

        @self.app.get("/test-success-notice")
        def show_success_notice():
            flash("Credit sale recorded.", "success")
            return render_template("base.html")

        self.client = self.app.test_client()

    def test_naive_utc_sale_time_is_formatted_in_nairobi_time(self):
        recorded_utc = datetime(2026, 10, 19, 12, 0)

        self.assertEqual(
            format_nairobi_datetime(recorded_utc, "%d %b %Y %H:%M %Z"),
            "19 Oct 2026 15:00 EAT",
        )

    def test_aware_utc_datetime_is_converted_to_nairobi_time(self):
        recorded_utc = datetime(2026, 10, 19, 12, 0, tzinfo=timezone.utc)

        self.assertEqual(
            format_nairobi_datetime(recorded_utc, "%H:%M"),
            "15:00",
        )

    def test_success_notice_has_unique_style_and_twenty_second_dismissal(self):
        response = self.client.get("/test-success-notice")

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"app-flash-success", response.data)
        self.assertIn(b"app-flash-progress", response.data)
        self.assertIn(b"flash-messages.js", response.data)

        with open("app/static/js/flash-messages.js", encoding="utf-8") as source:
            self.assertIn("dismiss, 20000", source.read())


if __name__ == "__main__":
    unittest.main()
