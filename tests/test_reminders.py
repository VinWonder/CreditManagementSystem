import unittest
import re
from unittest.mock import patch

from flask_login import login_user

from app import create_app
from app.extensions import db
from app.models import ReminderRule, Retailer


class TestConfig:
    APP_ENV = "testing"
    SECRET_KEY = "test-secret"
    SQLALCHEMY_DATABASE_URI = "sqlite://"
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    SQLALCHEMY_ENGINE_OPTIONS = {}
    AUTO_CREATE_DB = False
    SCHEDULER_ENABLED = False
    WTF_CSRF_ENABLED = True


class ReminderRulePageTests(unittest.TestCase):
    def setUp(self):
        self.app = create_app(TestConfig)
        self.app_context = self.app.app_context()
        self.app_context.push()
        db.create_all()
        self.client = self.app.test_client()

        self.retailer = Retailer(
            business_name="Test Shop",
            owner_name="Test Owner",
            phone_number="+254700000021",
            password_hash="test-hash",
        )
        db.session.add(self.retailer)
        db.session.commit()
        retailer_id = self.retailer.retailer_id

        @self.app.before_request
        def authenticate_test_retailer():
            login_user(db.session.get(Retailer, retailer_id))

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.app_context.pop()

    def _add_rule(self, *, active=True, retailer_id=None):
        rule = ReminderRule(
            retailer_id=retailer_id or self.retailer.retailer_id,
            trigger_days_overdue=7,
            message_template="Hello {customer_name}, balance {balance}",
            channel="sms",
            active=active,
        )
        db.session.add(rule)
        db.session.commit()
        return rule

    def _csrf_token(self):
        response = self.client.get("/reminders/rules")
        match = re.search(rb'name="csrf_token" type="hidden" value="([^"]+)"', response.data)
        self.assertIsNotNone(match)
        return match.group(1).decode()

    def test_rules_page_shows_rule_counts_and_csrf_protected_actions(self):
        self._add_rule(active=True)
        self._add_rule(active=False)

        response = self.client.get("/reminders/rules")

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Active rules", response.data)
        self.assertIn(b"Paused rules", response.data)
        self.assertIn(b"Hello {customer_name}, balance {balance}", response.data)
        self.assertIn(b"name=\"csrf_token\"", response.data)
        self.assertIn(b"Matching customers may receive a real SMS or WhatsApp message", response.data)
        self.assertIn(b"08:00", response.data)

    @patch("app.reminders.routes.run_ageing_analysis_and_dispatch")
    def test_manual_scan_without_active_rules_does_not_run(self, run_scan):
        response = self.client.post(
            "/reminders/run-now", data={"csrf_token": self._csrf_token()}
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.headers["Location"], "/reminders/rules")
        run_scan.assert_not_called()

    @patch("app.reminders.routes.run_ageing_analysis_and_dispatch")
    def test_manual_scan_rejects_request_without_csrf_token(self, run_scan):
        self._add_rule(active=True)

        response = self.client.post("/reminders/run-now", data={})

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.headers["Location"], "/reminders/rules")
        run_scan.assert_not_called()

    @patch(
        "app.reminders.routes.run_ageing_analysis_and_dispatch",
        return_value={"scanned": 3, "sent": 2, "failed": 1},
    )
    def test_manual_scan_runs_for_signed_in_retailer_when_rule_active(self, run_scan):
        self._add_rule(active=True)

        response = self.client.post(
            "/reminders/run-now", data={"csrf_token": self._csrf_token()}
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.headers["Location"], "/reminders/log")
        run_scan.assert_called_once_with(retailer_id=self.retailer.retailer_id)

    def test_delete_rule_is_limited_to_the_signed_in_retailer(self):
        other_retailer = Retailer(
            business_name="Other Shop",
            owner_name="Other Owner",
            phone_number="+254700000022",
            password_hash="test-hash",
        )
        db.session.add(other_retailer)
        db.session.commit()
        rule = self._add_rule(retailer_id=other_retailer.retailer_id)

        response = self.client.post(f"/reminders/rules/{rule.rule_id}/delete", data={})

        self.assertEqual(response.status_code, 302)
        self.assertIsNotNone(db.session.get(ReminderRule, rule.rule_id))

    def test_delete_rule_rejects_request_without_csrf_token(self):
        rule = self._add_rule()

        response = self.client.post(f"/reminders/rules/{rule.rule_id}/delete", data={})

        self.assertEqual(response.status_code, 302)
        self.assertIsNotNone(db.session.get(ReminderRule, rule.rule_id))

    def test_delete_rule_accepts_csrf_token_for_owned_rule(self):
        rule = self._add_rule()
        token = self._csrf_token()

        response = self.client.post(
            f"/reminders/rules/{rule.rule_id}/delete",
            data={"csrf_token": token},
        )

        self.assertEqual(response.status_code, 302)
        self.assertIsNone(db.session.get(ReminderRule, rule.rule_id))


if __name__ == "__main__":
    unittest.main()
