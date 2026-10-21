import unittest
from datetime import datetime, timedelta
from decimal import Decimal

from flask import g
from flask_login import login_user

from app import create_app
from app.extensions import db
from app.models import CreditTransaction, Customer, Ledger, MessageDispatchLog, Payment, Retailer


class PaymentTimeConfig:
    APP_ENV = "testing"
    SECRET_KEY = "test-secret"
    SQLALCHEMY_DATABASE_URI = "sqlite://"
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    SQLALCHEMY_ENGINE_OPTIONS = {}
    AUTO_CREATE_DB = False
    WTF_CSRF_ENABLED = False
    DEFAULT_PHONE_REGION = "KE"


class PaymentTimesShowInEatTests(unittest.TestCase):
    """Times are stored in UTC; every page must show them in Nairobi time (UTC+3)."""

    def setUp(self):
        self.app = create_app(PaymentTimeConfig)
        self.app_context = self.app.app_context()
        self.app_context.push()
        db.create_all()
        self.client = self.app.test_client()
        self.retailer = Retailer(business_name="Time Shop", owner_name="Owner",
                                 phone_number="+254700000071", password_hash="x")
        db.session.add(self.retailer)
        db.session.flush()
        self.customer = Customer(retailer_id=self.retailer.retailer_id, full_name="Night Buyer",
                                 phone_number="+254712345678")
        db.session.add(self.customer)
        db.session.flush()
        db.session.add_all([
            CreditTransaction(retailer_id=self.retailer.retailer_id, customer_id=self.customer.customer_id,
                              item_description="Rice", amount=Decimal("500"),
                              transaction_date=datetime(2026, 10, 20, 8, 0)),
            Ledger(retailer_id=self.retailer.retailer_id, customer_id=self.customer.customer_id),
            # 21:30 UTC on the 21st is 00:30 EAT on the 22nd.
            Payment(retailer_id=self.retailer.retailer_id, customer_id=self.customer.customer_id,
                    amount_paid=Decimal("100"), payment_method="cash", approval_status="approved",
                    payment_date=datetime(2026, 10, 21, 21, 30),
                    reviewed_by_retailer_id=self.retailer.retailer_id,
                    reviewed_at=datetime(2026, 10, 21, 21, 31)),
            MessageDispatchLog(customer_id=self.customer.customer_id, channel="sms", status="sent",
                               message_content="Reminder", sent_at=datetime(2026, 10, 21, 7, 5)),
        ])
        db.session.commit()

        @self.app.before_request
        def authenticate():
            g.pop("_login_user", None)
            login_user(db.session.get(Retailer, self.retailer.retailer_id))

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.app_context.pop()

    def _page(self, url):
        return self.client.get(url).get_data(as_text=True)

    def test_payment_history_shows_the_nairobi_date_and_time(self):
        page = self._page("/payments/")

        self.assertIn("22 Oct 2026", page)
        self.assertIn("00:30 EAT", page)
        self.assertIn("Reviewed 22 Oct 2026 · 00:31 EAT", page)
        self.assertNotIn("21:30", page)

    def test_customer_page_shows_the_payment_time_in_eat(self):
        page = self._page(f"/customers/{self.customer.customer_id}")

        self.assertIn("22 Oct 2026 · 00:30 EAT", page)

    def test_dashboard_shows_the_nairobi_payment_date(self):
        page = self._page("/dashboard")
        recent = page.split("Recent payments")[1]

        self.assertIn("<strong>22</strong><span>Oct 2026</span>", recent)

    def test_dispatch_history_shows_eat(self):
        page = self._page("/reminders/log")

        self.assertIn("21 Oct 2026 · 10:05 EAT", page)

    def test_new_payments_are_stamped_with_the_current_time(self):
        before = datetime.utcnow() - timedelta(seconds=1)
        self.client.post("/payments/new", data={
            "customer_id": str(self.customer.customer_id), "amount_paid": "50", "payment_method": "cash",
        })
        after = datetime.utcnow() + timedelta(seconds=1)

        newest = Payment.query.order_by(Payment.payment_id.desc()).first()
        self.assertEqual(newest.amount_paid, Decimal("50.00"))
        self.assertTrue(before <= newest.payment_date <= after, newest.payment_date)


if __name__ == "__main__":
    unittest.main()
