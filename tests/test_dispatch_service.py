import unittest
from types import SimpleNamespace
from unittest.mock import patch

from flask import Flask

from app.customers.forms import CustomerForm
from app.reminders.dispatch_service import dispatch_reminder


class CustomerReminderChannelTests(unittest.TestCase):
    def setUp(self):
        self.app = Flask(__name__)
        self.app.config["SECRET_KEY"] = "test-secret"

    def test_customer_form_offers_both_channels(self):
        with self.app.test_request_context():
            form = CustomerForm()

        self.assertIn(("both", "SMS + WhatsApp"), form.preferred_channel.choices)

    @patch("app.reminders.dispatch_service.db.session.commit")
    @patch("app.reminders.dispatch_service.db.session.add")
    @patch("app.reminders.dispatch_service.send_whatsapp", return_value=(True, "WhatsApp sent"))
    @patch("app.reminders.dispatch_service.send_sms", return_value=(True, "SMS sent"))
    def test_both_preference_dispatches_and_logs_both_channels(
        self, send_sms, send_whatsapp, add_log, commit
    ):
        customer = SimpleNamespace(
            customer_id=12,
            phone_number="+254700000000",
            preferred_channel="both",
        )
        rule = SimpleNamespace(rule_id=4, channel="both")

        with self.app.app_context():
            success, info = dispatch_reminder(customer, rule, "Payment reminder")

        self.assertTrue(success)
        self.assertEqual(info, "SMS sent; WhatsApp sent")
        send_sms.assert_called_once_with(customer.phone_number, "Payment reminder")
        send_whatsapp.assert_called_once_with(customer.phone_number, "Payment reminder")
        self.assertEqual(
            [call.args[0].channel for call in add_log.call_args_list],
            ["sms", "whatsapp"],
        )
        self.assertEqual(
            [call.args[0].status for call in add_log.call_args_list],
            ["sent", "sent"],
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
        commit.assert_called_once()


if __name__ == "__main__":
    unittest.main()
