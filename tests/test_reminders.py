import unittest
import re
from datetime import datetime, timedelta
from decimal import Decimal
from unittest.mock import patch

from flask_login import login_user

from app import create_app
from app.extensions import db
from app.models import (
    CreditTransaction,
    Customer,
    Ledger,
    MessageDispatchLog,
    ReminderRule,
    Retailer,
)
from app.reminders.engine import (
    run_ageing_analysis_and_dispatch,
    select_matching_rule,
)


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

    def _add_dispatch(self, *, name, phone, status="sent", channel="sms", retailer_id=None,
                      sent_at=None, rule=None):
        customer = Customer(
            retailer_id=retailer_id or self.retailer.retailer_id,
            full_name=name,
            phone_number=phone,
        )
        db.session.add(customer)
        db.session.flush()
        dispatch = MessageDispatchLog(
            customer_id=customer.customer_id,
            rule_id=rule.rule_id if rule else None,
            channel=channel,
            message_content=f"Hello {name}, your balance is KES 500.",
            status=status,
            sent_at=sent_at or datetime.utcnow(),
        )
        db.session.add(dispatch)
        db.session.commit()
        return dispatch

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
        self.assertIn(b"Create reminder rule", response.data)

    def test_new_rule_form_shows_placeholder_guidance_and_preview(self):
        response = self.client.get("/reminders/rules/new")

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Available placeholders", response.data)
        self.assertIn(b"reminder-message-preview", response.data)
        self.assertIn(b"Activate this rule", response.data)
        self.assertIn(b"data-trigger-days=\"7\"", response.data)

    def test_new_rule_accepts_supported_placeholders(self):
        response = self.client.post(
            "/reminders/rules/new",
            data={
                "csrf_token": self._csrf_token(),
                "trigger_days_overdue": "7",
                "channel": "both",
                "message_template": (
                    "Hello {customer_name} from {business_name}: "
                    "{balance}, overdue {days_overdue} days."
                ),
                "active": "y",
                "submit": "Save Rule",
            },
        )

        self.assertEqual(response.status_code, 302)
        rule = ReminderRule.query.one()
        self.assertEqual(rule.channel, "both")
        self.assertTrue(rule.active)
        self.assertIn("{days_overdue}", rule.message_template)

    def test_new_rule_allows_first_day_after_credit_sale(self):
        response = self.client.post(
            "/reminders/rules/new",
            data={
                "csrf_token": self._csrf_token(),
                "trigger_days_overdue": "1",
                "channel": "sms",
                "message_template": "Hello {customer_name}, balance {balance}.",
                "active": "y",
                "submit": "Save Rule",
            },
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(ReminderRule.query.one().trigger_days_overdue, 1)
        rules_page = self.client.get("/reminders/rules")
        self.assertIn(b"First day after sale", rules_page.data)

    def test_new_rule_rejects_unsupported_placeholder(self):
        response = self.client.post(
            "/reminders/rules/new",
            data={
                "csrf_token": self._csrf_token(),
                "trigger_days_overdue": "7",
                "channel": "sms",
                "message_template": "Hello {customer_nam}.",
                "active": "y",
                "submit": "Save Rule",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Unsupported placeholder", response.data)
        self.assertEqual(ReminderRule.query.count(), 0)

    def test_new_rule_rejects_malformed_placeholder(self):
        response = self.client.post(
            "/reminders/rules/new",
            data={
                "csrf_token": self._csrf_token(),
                "trigger_days_overdue": "7",
                "channel": "sms",
                "message_template": "Hello {customer_name.",
                "active": "y",
                "submit": "Save Rule",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Placeholders must use complete braces", response.data)
        self.assertEqual(ReminderRule.query.count(), 0)

    def test_new_rule_rejects_duplicate_overdue_threshold(self):
        self._add_rule()

        response = self.client.post(
            "/reminders/rules/new",
            data={
                "csrf_token": self._csrf_token(),
                "trigger_days_overdue": "7",
                "channel": "whatsapp",
                "message_template": "Hello {customer_name}.",
                "active": "y",
                "submit": "Save Rule",
            },
        )

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"A rule already exists for this overdue threshold", response.data)
        self.assertEqual(ReminderRule.query.count(), 1)

    def test_dispatch_log_shows_delivery_context_and_summary(self):
        rule = self._add_rule()
        self._add_dispatch(
            name="Amina Hassan",
            phone="+254700000081",
            rule=rule,
        )
        self._add_dispatch(
            name="Baraka Otieno",
            phone="+254700000082",
            status="failed",
            channel="whatsapp",
            rule=rule,
        )

        response = self.client.get("/reminders/log")

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Dispatch history", response.data)
        self.assertIn(b"Total attempts", response.data)
        self.assertIn(b"Amina Hassan", response.data)
        self.assertIn(b"+254700000081", response.data)
        self.assertIn(b"7-day rule", response.data)
        self.assertIn(b"Accepted", response.data)
        self.assertIn(b"Failed", response.data)
        self.assertIn(b'name="status"', response.data)

    def test_dispatch_log_filters_records_by_status_channel_and_search(self):
        self._add_dispatch(
            name="Amina Hassan",
            phone="+254700000083",
            status="sent",
            channel="sms",
        )
        self._add_dispatch(
            name="Baraka Otieno",
            phone="+254700000084",
            status="failed",
            channel="whatsapp",
        )

        response = self.client.get(
            "/reminders/log?status=failed&channel=whatsapp&q=Baraka"
        )

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Baraka Otieno", response.data)
        self.assertNotIn(b"Amina Hassan", response.data)
        self.assertIn(b"Total attempts", response.data)
        self.assertIn(b"2", response.data)

    def test_dispatch_log_is_tenant_scoped_and_paginated(self):
        for index in range(27):
            self._add_dispatch(
                name=f"Customer {index:02}",
                phone=f"+254711000{index:03}",
                sent_at=datetime.utcnow() - timedelta(minutes=index),
            )
        other_retailer = Retailer(
            business_name="Other Shop",
            owner_name="Other Owner",
            phone_number="+254700000085",
            password_hash="test-hash",
        )
        db.session.add(other_retailer)
        db.session.commit()
        self._add_dispatch(
            name="Other Tenant",
            phone="+254700000086",
            retailer_id=other_retailer.retailer_id,
        )

        first_page = self.client.get("/reminders/log")
        second_page = self.client.get("/reminders/log?page=2")

        self.assertEqual(first_page.status_code, 200)
        self.assertIn(b"Page 1 of 2", first_page.data)
        self.assertIn(b"Page 2 of 2", second_page.data)
        self.assertIn(b"Total attempts", first_page.data)
        self.assertNotIn(b"Other Tenant", first_page.data)
        self.assertNotIn(b"Other Tenant", second_page.data)
        self.assertEqual(first_page.data.count(b"class=\"dispatch-entry\""), 25)
        self.assertEqual(second_page.data.count(b"class=\"dispatch-entry\""), 2)

    @patch(
        "app.reminders.dispatch_service.send_whatsapp",
        return_value=(True, "accepted"),
    )
    def test_personal_reminder_uses_saved_channel_and_logs_current_balance(
        self, send_whatsapp
    ):
        customer = Customer(
            retailer_id=self.retailer.retailer_id,
            full_name="Amina Hassan",
            phone_number="+254700000091",
            preferred_channel="whatsapp",
        )
        db.session.add(customer)
        db.session.flush()
        db.session.add(
            CreditTransaction(
                retailer_id=self.retailer.retailer_id,
                customer_id=customer.customer_id,
                item_description="Water pipe",
                amount=Decimal("500.00"),
                transaction_date=datetime.utcnow() - timedelta(days=12),
            )
        )
        db.session.add(
            Ledger(
                retailer_id=self.retailer.retailer_id,
                customer_id=customer.customer_id,
                outstanding_balance=Decimal("500.00"),
            )
        )
        rule = ReminderRule(
            retailer_id=self.retailer.retailer_id,
            trigger_days_overdue=7,
            message_template=(
                "Hello {customer_name}, {business_name} balance: "
                "{balance}; overdue {days_overdue} days."
            ),
            channel="whatsapp",
            active=True,
        )
        db.session.add(rule)
        db.session.commit()
        account_page = self.client.get(f"/customers/{customer.customer_id}")
        self.assertEqual(account_page.status_code, 200)
        self.assertIn(b"Send a personal reminder", account_page.data)
        self.assertIn(b"saved message", account_page.data)
        token = self._csrf_token()

        response = self.client.post(
            f"/reminders/customers/{customer.customer_id}/send-personal",
            data={"csrf_token": token},
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.headers["Location"], "/reminders/log")
        send_whatsapp.assert_called_once()
        sent_message = send_whatsapp.call_args.args[1]
        self.assertEqual(
            sent_message,
            "Hello Amina Hassan, Test Shop balance: KES 500.00; "
            "overdue 12 days.\n"
            "If you have already paid, please ignore this message. Thank you.",
        )
        dispatch = MessageDispatchLog.query.one()
        self.assertEqual(dispatch.channel, "whatsapp")
        self.assertEqual(dispatch.rule_id, rule.rule_id)
        self.assertEqual(dispatch.status, "sent")

    @patch("app.reminders.dispatch_service.send_sms")
    def test_personal_reminder_does_not_send_without_matching_active_rule(self, send_sms):
        customer = Customer(
            retailer_id=self.retailer.retailer_id,
            full_name="Not Yet Overdue",
            phone_number="+254700000094",
            preferred_channel="sms",
        )
        db.session.add(customer)
        db.session.flush()
        db.session.add(
            CreditTransaction(
                retailer_id=self.retailer.retailer_id,
                customer_id=customer.customer_id,
                item_description="Older purchase",
                amount=Decimal("500.00"),
                transaction_date=datetime.utcnow() - timedelta(days=2),
            )
        )
        db.session.add(
            Ledger(
                retailer_id=self.retailer.retailer_id,
                customer_id=customer.customer_id,
                outstanding_balance=Decimal("500.00"),
                days_overdue=0,
            )
        )
        self._add_rule(active=False)
        db.session.commit()

        response = self.client.post(
            f"/reminders/customers/{customer.customer_id}/send-personal",
            data={"csrf_token": self._csrf_token()},
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.headers["Location"], f"/customers/{customer.customer_id}")
        send_sms.assert_not_called()
        self.assertEqual(MessageDispatchLog.query.count(), 0)

    @patch("app.reminders.engine.dispatch_reminder", return_value=(True, "accepted"))
    def test_scheduled_reminders_use_rule_message_and_required_disclaimer(self, dispatch):
        customer = Customer(
            retailer_id=self.retailer.retailer_id,
            full_name="Scheduled Customer",
            phone_number="+254700000097",
            preferred_channel="sms",
        )
        db.session.add(customer)
        db.session.flush()
        db.session.add(
            CreditTransaction(
                retailer_id=self.retailer.retailer_id,
                customer_id=customer.customer_id,
                item_description="Older purchase",
                amount=Decimal("275.00"),
                transaction_date=datetime.utcnow() - timedelta(days=12),
            )
        )
        db.session.add(
            Ledger(
                retailer_id=self.retailer.retailer_id,
                customer_id=customer.customer_id,
                outstanding_balance=Decimal("275.00"),
            )
        )
        rule = ReminderRule(
            retailer_id=self.retailer.retailer_id,
            trigger_days_overdue=7,
            message_template=(
                "Rule text for {customer_name} at {business_name}: {balance}, "
                "{days_overdue} days."
            ),
            channel="sms",
            active=True,
        )
        db.session.add(rule)
        db.session.commit()

        result = run_ageing_analysis_and_dispatch(self.retailer.retailer_id)

        self.assertEqual(result, {"scanned": 1, "sent": 1, "failed": 0})
        dispatch.assert_called_once()
        self.assertEqual(dispatch.call_args.args[1].rule_id, rule.rule_id)
        self.assertEqual(
            dispatch.call_args.args[2],
            "Rule text for Scheduled Customer at Test Shop: KES 275.00, 12 days.\n"
            "If you have already paid, please ignore this message. Thank you.",
        )

    @patch("app.reminders.dispatch_service.send_whatsapp", return_value=(True, "accepted"))
    def test_live_test_api_uses_selected_opted_in_channel_and_rule_template(self, send_whatsapp):
        customer = Customer(
            retailer_id=self.retailer.retailer_id,
            full_name="Amina Hassan",
            phone_number="+254700000095",
            preferred_channel="both",
        )
        db.session.add(customer)
        db.session.flush()
        db.session.add(
            CreditTransaction(
                retailer_id=self.retailer.retailer_id,
                customer_id=customer.customer_id,
                item_description="Test item",
                amount=Decimal("350.00"),
                transaction_date=datetime.utcnow() - timedelta(days=12),
            )
        )
        db.session.add(
            Ledger(
                retailer_id=self.retailer.retailer_id,
                customer_id=customer.customer_id,
                outstanding_balance=Decimal("350.00"),
            )
        )
        rule = ReminderRule(
            retailer_id=self.retailer.retailer_id,
            trigger_days_overdue=7,
            message_template="Exact: {customer_name} owes {balance}.",
            channel="sms",
            active=True,
        )
        db.session.add(rule)
        db.session.commit()
        self.app.config["WHATSAPP_TOKEN"] = "test-token"
        self.app.config["WHATSAPP_PHONE_ID"] = "test-phone-id"

        response = self.client.post(
            f"/reminders/api/customers/{customer.customer_id}/test",
            json={"channel": "whatsapp", "confirm": True},
            headers={"X-CSRFToken": self._csrf_token()},
        )

        self.assertEqual(response.status_code, 202)
        self.assertEqual(response.json["status"], "accepted")
        self.assertIn("does not confirm handset delivery", response.json["message"])
        send_whatsapp.assert_called_once()
        self.assertEqual(
            send_whatsapp.call_args.args[1],
            "Exact: Amina Hassan owes KES 350.00.\n"
            "If you have already paid, please ignore this message. Thank you.",
        )
        dispatch = MessageDispatchLog.query.one()
        self.assertEqual(dispatch.channel, "whatsapp")
        self.assertEqual(dispatch.rule_id, rule.rule_id)

    def test_live_test_api_rejects_missing_confirmation_and_unconfigured_provider(self):
        customer = Customer(
            retailer_id=self.retailer.retailer_id,
            full_name="SMS Test",
            phone_number="+254700000096",
            preferred_channel="sms",
        )
        db.session.add(customer)
        db.session.flush()
        db.session.add(
            CreditTransaction(
                retailer_id=self.retailer.retailer_id,
                customer_id=customer.customer_id,
                item_description="Test item",
                amount=Decimal("100.00"),
                transaction_date=datetime.utcnow() - timedelta(days=12),
            )
        )
        db.session.add(
            Ledger(
                retailer_id=self.retailer.retailer_id,
                customer_id=customer.customer_id,
                outstanding_balance=Decimal("100.00"),
            )
        )
        self._add_rule(active=True)
        db.session.commit()
        headers = {"X-CSRFToken": self._csrf_token()}

        unconfirmed = self.client.post(
            f"/reminders/api/customers/{customer.customer_id}/test",
            json={"channel": "sms", "confirm": False},
            headers=headers,
        )
        unconfigured = self.client.post(
            f"/reminders/api/customers/{customer.customer_id}/test",
            json={"channel": "sms", "confirm": True},
            headers=headers,
        )

        self.assertEqual(unconfirmed.status_code, 400)
        self.assertEqual(unconfigured.status_code, 503)
        self.assertEqual(MessageDispatchLog.query.count(), 0)

    def test_provider_status_api_never_returns_credentials(self):
        self.app.config["WHATSAPP_TOKEN"] = "do-not-expose"
        self.app.config["WHATSAPP_PHONE_ID"] = "phone-id"

        response = self.client.get("/reminders/api/providers/status")

        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json["providers"]["whatsapp"]["configured"])
        self.assertNotIn(b"do-not-expose", response.data)

    @patch("app.reminders.dispatch_service.send_sms")
    def test_personal_reminder_does_not_send_for_cleared_account(self, send_sms):
        customer = Customer(
            retailer_id=self.retailer.retailer_id,
            full_name="Paid Customer",
            phone_number="+254700000092",
            preferred_channel="sms",
        )
        db.session.add(customer)
        db.session.flush()
        db.session.add(
            Ledger(
                retailer_id=self.retailer.retailer_id,
                customer_id=customer.customer_id,
                outstanding_balance=Decimal("0.00"),
            )
        )
        db.session.commit()

        response = self.client.post(
            f"/reminders/customers/{customer.customer_id}/send-personal",
            data={"csrf_token": self._csrf_token()},
        )

        self.assertEqual(response.status_code, 302)
        send_sms.assert_not_called()
        self.assertEqual(MessageDispatchLog.query.count(), 0)

    @patch("app.reminders.dispatch_service.send_sms")
    def test_personal_reminder_requires_a_valid_saved_channel(self, send_sms):
        customer = Customer(
            retailer_id=self.retailer.retailer_id,
            full_name="Missing Preference",
            phone_number="+254700000093",
            preferred_channel=None,
        )
        db.session.add(customer)
        db.session.commit()

        response = self.client.post(
            f"/reminders/customers/{customer.customer_id}/send-personal",
            data={"csrf_token": self._csrf_token()},
        )

        self.assertEqual(response.status_code, 302)
        send_sms.assert_not_called()
        self.assertEqual(MessageDispatchLog.query.count(), 0)

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


class ReminderRuleSelectionTests(unittest.TestCase):
    def test_selects_only_the_most_specific_matching_threshold(self):
        rules = [
            ReminderRule(trigger_days_overdue=3),
            ReminderRule(trigger_days_overdue=7),
            ReminderRule(trigger_days_overdue=14),
        ]

        selected = select_matching_rule(rules, days_overdue=10)

        self.assertEqual(selected.trigger_days_overdue, 7)

    def test_returns_no_rule_when_threshold_has_not_been_reached(self):
        rules = [ReminderRule(trigger_days_overdue=7)]

        self.assertIsNone(select_matching_rule(rules, days_overdue=6))


if __name__ == "__main__":
    unittest.main()
