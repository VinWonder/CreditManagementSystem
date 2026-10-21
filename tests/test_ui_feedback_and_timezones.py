import unittest
from datetime import datetime, timezone

from flask import flash, render_template

from app import create_app
from app.timezones import days_since_nairobi_date, format_nairobi_datetime


class UiFeedbackConfig:
    APP_ENV = "testing"
    SECRET_KEY = "test-secret"
    SQLALCHEMY_DATABASE_URI = "sqlite://"
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    SQLALCHEMY_ENGINE_OPTIONS = {}
    AUTO_CREATE_DB = False
    WTF_CSRF_ENABLED = False


class UiFeedbackAndTimezoneTests(unittest.TestCase):
    def setUp(self):
        self.app = create_app(UiFeedbackConfig)

        @self.app.get("/test-success-notice")
        def show_success_notice():
            flash("Credit sale recorded.", "success")
            return render_template("base.html")

        @self.app.get("/test-notice/<category>")
        def show_notice(category):
            flash("You have been logged out.", category)
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

    def test_first_reminder_day_uses_nairobi_calendar_date_not_24_hours(self):
        sale_just_before_midnight = datetime(
            2026, 10, 18, 20, 50, tzinfo=timezone.utc
        )
        next_day_just_after_midnight = datetime(
            2026, 10, 18, 21, 10, tzinfo=timezone.utc
        )

        self.assertEqual(
            days_since_nairobi_date(
                sale_just_before_midnight, next_day_just_after_midnight
            ),
            1,
        )

    def test_success_notice_sits_in_the_bottom_stack_and_dismisses_after_five_seconds(self):
        response = self.client.get("/test-success-notice")

        self.assertEqual(response.status_code, 200)
        self.assertIn(b'class="app-flash-stack"', response.data)
        self.assertIn(b"app-flash-success app-flash-auto-dismiss", response.data)
        self.assertIn(b"app-flash-progress", response.data)
        self.assertIn(b"flash-messages.js", response.data)

        with open("app/static/js/flash-messages.js", encoding="utf-8") as source:
            self.assertIn("AUTO_DISMISS_MS = 5000", source.read())

    def test_logout_info_notice_also_dismisses_itself(self):
        response = self.client.get("/test-notice/info")

        self.assertIn(b"app-flash-info app-flash-auto-dismiss", response.data)
        self.assertIn(b"app-flash-progress", response.data)

    def test_warnings_stay_until_the_user_closes_them(self):
        response = self.client.get("/test-notice/warning")

        self.assertIn(b"app-flash-warning", response.data)
        self.assertNotIn(b"app-flash-auto-dismiss", response.data)
        self.assertNotIn(b"app-flash-progress", response.data)

    def test_welcome_preview_uses_shop_workflow_instead_of_scary_debt_total(self):
        response = self.client.get("/")

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Every account,", response.data)
        self.assertIn(b"Sales", response.data)
        self.assertIn(b"Payments", response.data)
        self.assertIn(b"Follow-up", response.data)
        self.assertNotIn(b"KES 18,450", response.data)
        self.assertNotIn(b"OUTSTANDING CREDIT", response.data)


if __name__ == "__main__":
    unittest.main()
