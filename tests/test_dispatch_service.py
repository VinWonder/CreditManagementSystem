import unittest
from types import SimpleNamespace
from unittest.mock import patch

from flask import Flask
from requests import HTTPError

from app.customers.forms import CustomerForm
from app.reminders.dispatch_service import (
    _provider_request_error,
    dispatch_reminder,
    send_whatsapp,
)


class CustomerReminderChannelTests(unittest.TestCase):
    def setUp(self):
        self.app = Flask(__name__)
        self.app.config["SECRET_KEY"] = "test-secret"

    def test_customer_form_offers_both_channels(self):
        with self.app.test_request_context():
            form = CustomerForm()

        self.assertIn(("both", "SMS + WhatsApp"), form.preferred_channel.choices)
        self.assertIn("shop_name", form._fields)

    @patch("app.reminders.dispatch_service.db.session.commit")
    @patch("app.reminders.dispatch_service.db.session.add")
    @patch("app.reminders.dispatch_service.send_whatsapp", return_value=(True, "WhatsApp sent"))
    @patch("app.reminders.dispatch_service.send_sms", return_value=(True, "SMS sent"))
    def test_both_preference_dispatches_and_logs_both_channels(
        self, send_sms, send_whatsapp, add_log, commit
    ):
        customer = SimpleNamespace(
            customer_id=12,
            retailer_id=8,
            phone_number="+254700000000",
            preferred_channel="both",
        )
        rule = SimpleNamespace(rule_id=4, channel="both")

        with self.app.app_context():
            success, info = dispatch_reminder(customer, rule, "Payment reminder")

        self.assertTrue(success)
        self.assertEqual(info, "SMS sent; WhatsApp sent")
        send_sms.assert_called_once_with(
            customer.phone_number, "Payment reminder", retailer_id=8
        )
        send_whatsapp.assert_called_once_with(
            customer.phone_number, "Payment reminder", retailer_id=8
        )
        self.assertEqual(
            [call.args[0].channel for call in add_log.call_args_list],
            ["sms", "whatsapp"],
        )
        self.assertEqual(
            [
                (call.args[0].status, call.args[0].failure_reason)
                for call in add_log.call_args_list
            ],
            [("sent", None), ("sent", None)],
        )
        commit.assert_called_once()

    @patch("app.reminders.dispatch_service.db.session.commit")
    @patch("app.reminders.dispatch_service.db.session.add")
    @patch("app.reminders.dispatch_service.send_whatsapp", return_value=(True, "WhatsApp sent"))
    @patch("app.reminders.dispatch_service.send_sms", return_value=(False, "SMS failed"))
    def test_partial_failure_is_reported_and_logged_per_channel(
        self, send_sms, send_whatsapp, add_log, commit
    ):
        customer = SimpleNamespace(
            customer_id=12,
            retailer_id=8,
            phone_number="+254700000000",
            preferred_channel="both",
        )
        rule = SimpleNamespace(rule_id=4, channel="both")

        with self.app.app_context():
            success, _info = dispatch_reminder(customer, rule, "Payment reminder")

        self.assertFalse(success)
        self.assertEqual(
            [(call.args[0].channel, call.args[0].status) for call in add_log.call_args_list],
            [("sms", "failed"), ("whatsapp", "sent")],
        )
        self.assertEqual(add_log.call_args_list[0].args[0].failure_reason, "SMS failed")
        self.assertIsNone(add_log.call_args_list[1].args[0].failure_reason)
        commit.assert_called_once()

    def test_provider_http_error_uses_safe_actionable_message(self):
        response = SimpleNamespace(
            status_code=400,
            json=lambda: {"error": {"message": "Recipient is not allowed"}},
        )
        error = HTTPError(response=response)

        detail = _provider_request_error(error, "WhatsApp")

        self.assertEqual(
            detail,
            "WhatsApp rejected the request: Recipient is not allowed",
        )

    @patch("app.reminders.dispatch_service.requests.post")
    def test_whatsapp_request_uses_configured_bearer_token(self, post):
        post.return_value.raise_for_status.return_value = None
        self.app.config.update(
            WHATSAPP_TOKEN="test-access-token",
            WHATSAPP_PHONE_ID="test-phone-id",
        )

        with self.app.app_context():
            accepted, _info = send_whatsapp("+254700000000", "Reminder")

        self.assertTrue(accepted)
        self.assertEqual(
            post.call_args.kwargs["headers"]["Authorization"],
            "Bearer test-access-token",
        )


if __name__ == "__main__":
    unittest.main()
