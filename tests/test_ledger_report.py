import unittest

from flask_login import login_user

from app import create_app
from app.extensions import db
from app.models import Customer, Ledger, Retailer


class TestConfig:
    APP_ENV = "testing"
    SECRET_KEY = "test-secret"
    SQLALCHEMY_DATABASE_URI = "sqlite://"
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    SQLALCHEMY_ENGINE_OPTIONS = {}
    AUTO_CREATE_DB = False
    SCHEDULER_ENABLED = False
    WTF_CSRF_ENABLED = False


class ReceivablesReportTests(unittest.TestCase):
    def setUp(self):
        self.app = create_app(TestConfig)
        self.app_context = self.app.app_context()
        self.app_context.push()
        db.create_all()
        self.client = self.app.test_client()

        self.retailer = Retailer(
            business_name="Test Shop",
            owner_name="Test Owner",
            phone_number="+254700000011",
            password_hash="test-hash",
        )
        db.session.add(self.retailer)
        db.session.flush()
        self.retailer_id = self.retailer.retailer_id
        self.customer = self._add_customer("Amina Hassan", "+254700000012")
        self.other_customer = self._add_customer("Brian Otieno", "+254700000013")
        self._add_ledger(self.customer, "125.50", 12)
        self._add_ledger(self.other_customer, "80.00", 0)
        retailer_id = self.retailer_id

        @self.app.before_request
        def authenticate_test_retailer():
            login_user(db.session.get(Retailer, retailer_id))

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.app_context.pop()

    def _add_customer(self, name, phone):
        customer = Customer(
            retailer_id=self.retailer_id,
            full_name=name,
            phone_number=phone,
        )
        db.session.add(customer)
        db.session.flush()
        return customer

    def _add_ledger(self, customer, balance, days_overdue):
        db.session.add(
            Ledger(
                retailer_id=self.retailer_id,
                customer_id=customer.customer_id,
                outstanding_balance=balance,
                days_overdue=days_overdue,
            )
        )
        db.session.commit()

    def test_report_shows_account_summary_and_current_accounts(self):
        response = self.client.get("/ledger/report")

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"KES 205.50", response.data)
        self.assertIn(b"KES 125.50", response.data)
        self.assertIn(b"Accounts owing", response.data)
        self.assertIn(b"Not overdue", response.data)

    def test_overdue_filter_only_shows_overdue_balance_and_accounts(self):
        response = self.client.get("/ledger/report?overdue=1")

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Overdue accounts", response.data)
        self.assertIn(b"KES 125.50", response.data)
        self.assertNotIn(b"Brian Otieno", response.data)
        self.assertIn(b"12 days", response.data)

    def test_invalid_sort_uses_default_sort(self):
        response = self.client.get("/ledger/report?sort=unknown")

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Oldest overdue", response.data)

    def test_empty_overdue_report_has_a_clear_return_action(self):
        db.session.query(Ledger).filter_by(customer_id=self.customer.customer_id).update(
            {"days_overdue": 0}
        )
        db.session.commit()

        response = self.client.get("/ledger/report?overdue=1")

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"No overdue accounts", response.data)
        self.assertIn(b"View all balances", response.data)


if __name__ == "__main__":
    unittest.main()
