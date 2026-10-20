import unittest
from datetime import datetime, timedelta
from decimal import Decimal
from types import SimpleNamespace
from urllib.parse import parse_qs, unquote, urlsplit

from flask_login import login_user

from app import create_app
from app.extensions import db
from app.models import (
    CreditTransaction,
    Customer,
    Ledger,
    MessageDispatchLog,
    Payment,
    ReminderRule,
    Retailer,
    Shopkeeper,
)
from app.reminders.links import (
    normalize_msisdn,
    reminder_links,
    sms_link,
    whatsapp_link,
)


class TestConfig:
    APP_ENV = "testing"
    SECRET_KEY = "test-secret"
    SQLALCHEMY_DATABASE_URI = "sqlite://"
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    SQLALCHEMY_ENGINE_OPTIONS = {}
    AUTO_CREATE_DB = False
    WTF_CSRF_ENABLED = True


class MsisdnNormalizationTests(unittest.TestCase):
    def test_kenyan_local_number_becomes_international(self):
        self.assertEqual(normalize_msisdn("0712 345 678"), "254712345678")
        self.assertEqual(normalize_msisdn("+254712345678"), "254712345678")
        self.assertEqual(normalize_msisdn("254712345678"), "254712345678")
        self.assertEqual(normalize_msisdn("712345678"), "254712345678")
        self.assertEqual(normalize_msisdn("00254712345678"), "254712345678")

    def test_unusable_numbers_return_empty(self):
        self.assertEqual(normalize_msisdn(""), "")
        self.assertEqual(normalize_msisdn(None), "")
        self.assertEqual(normalize_msisdn("12345"), "")


class ReminderLinkTests(unittest.TestCase):
    def test_whatsapp_link_targets_customer_and_prefills_message(self):
        link = whatsapp_link("0712345678", "Dear Peter, you owe KES 500.00 & thanks")
        parts = urlsplit(link)
        self.assertEqual(parts.netloc, "wa.me")
        self.assertEqual(parts.path, "/254712345678")
        self.assertEqual(
            parse_qs(parts.query)["text"][0],
            "Dear Peter, you owe KES 500.00 & thanks",
        )

    def test_sms_link_targets_customer_and_prefills_body(self):
        link = sms_link("0712345678", "Reminder body")
        self.assertTrue(link.startswith("sms:+254712345678?body="))
        self.assertEqual(unquote(link.split("body=", 1)[1]), "Reminder body")

    def test_invalid_customer_number_yields_no_links(self):
        customer = SimpleNamespace(phone_number="")
        self.assertEqual(reminder_links(customer, "Hi"), {"whatsapp": None, "sms": None})


if __name__ == "__main__":
    unittest.main()


class CustomerReminderPageTests(unittest.TestCase):
    """The customer page offers deep links instead of sending through the API."""

    def setUp(self):
        self.app = create_app(TestConfig)
        self.app_context = self.app.app_context()
        self.app_context.push()
        db.create_all()
        self.client = self.app.test_client()
        self.retailer = Retailer(
            business_name="Mama Njeri Shop",
            owner_name="Njeri",
            phone_number="+254700000111",
            password_hash="test-hash",
        )
        db.session.add(self.retailer)
        db.session.commit()
        retailer_id = self.retailer.retailer_id

        @self.app.before_request
        def authenticate():
            login_user(db.session.get(Retailer, retailer_id))

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.app_context.pop()

    def _customer_with_balance(self, phone="+254712345678"):
        customer = Customer(
            retailer_id=self.retailer.retailer_id,
            full_name="Peter Kamau",
            phone_number=phone,
            preferred_channel="both",
        )
        db.session.add(customer)
        db.session.flush()
        db.session.add(
            CreditTransaction(
                retailer_id=self.retailer.retailer_id,
                customer_id=customer.customer_id,
                item_description="Sugar",
                amount=Decimal("500.00"),
                transaction_date=datetime.utcnow() - timedelta(days=9),
            )
        )
        db.session.add(
            Ledger(
                retailer_id=self.retailer.retailer_id,
                customer_id=customer.customer_id,
                outstanding_balance=Decimal("500.00"),
            )
        )
        db.session.add(
            ReminderRule(
                retailer_id=self.retailer.retailer_id,
                trigger_days_overdue=0,
                message_template="Dear {customer_name}, you owe {balance}.",
                channel="both",
                active=True,
            )
        )
        db.session.commit()
        return customer

    def test_page_offers_whatsapp_and_sms_links_for_the_customer(self):
        customer = self._customer_with_balance()

        response = self.client.get(f"/customers/{customer.customer_id}")
        html = response.get_data(as_text=True)

        self.assertEqual(response.status_code, 200)
        self.assertIn("Send on WhatsApp", html)
        self.assertIn("Send by SMS", html)
        self.assertIn("https://wa.me/254712345678?text=", html)
        self.assertIn("sms:+254712345678?body=", html)
        self.assertNotIn("is-disabled", html)

    def test_no_links_are_offered_when_the_balance_is_cleared(self):
        customer = self._customer_with_balance()
        db.session.add(
            Payment(
                retailer_id=self.retailer.retailer_id,
                customer_id=customer.customer_id,
                amount_paid=Decimal("500.00"),
            )
        )
        db.session.commit()

        html = self.client.get(f"/customers/{customer.customer_id}").get_data(as_text=True)

        self.assertNotIn("https://wa.me/", html)
        self.assertNotIn("sms:+254", html)

    def test_server_side_send_route_is_admin_only(self):
        customer = self._customer_with_balance()
        shopkeeper = Shopkeeper(
            retailer_id=self.retailer.retailer_id,
            full_name="Staff",
            phone_number="+254700000222",
            password_hash="test-hash",
        )
        db.session.add(shopkeeper)
        db.session.commit()

        shopkeeper_client = self.app.test_client()

        @self.app.before_request
        def authenticate_shopkeeper():
            login_user(db.session.get(Shopkeeper, shopkeeper.shopkeeper_id))

        response = shopkeeper_client.post(
            f"/reminders/customers/{customer.customer_id}/send-personal"
        )

        self.assertEqual(response.status_code, 403)
        self.assertEqual(MessageDispatchLog.query.count(), 0)
