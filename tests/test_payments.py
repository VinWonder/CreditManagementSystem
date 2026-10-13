import unittest
from werkzeug.datastructures import MultiDict

from app import create_app
from app.extensions import db
from app.models import CreditTransaction, Customer, Ledger, Payment, Retailer
from app.payments.forms import PaymentForm
from flask_login import login_user


class TestConfig:
    APP_ENV = "testing"
    SECRET_KEY = "test-secret"
    SQLALCHEMY_DATABASE_URI = "sqlite://"
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    SQLALCHEMY_ENGINE_OPTIONS = {}
    AUTO_CREATE_DB = False
    SCHEDULER_ENABLED = False
    WTF_CSRF_ENABLED = False


class PaymentFlowTests(unittest.TestCase):
    def setUp(self):
        self.app = create_app(TestConfig)
        self.app_context = self.app.app_context()
        self.app_context.push()
        db.create_all()
        self.client = self.app.test_client()

        self.retailer = Retailer(
            business_name="Test Shop",
            owner_name="Test Owner",
            phone_number="+254700000001",
            password_hash="test-hash",
        )
        db.session.add(self.retailer)
        db.session.flush()
        self.customer = Customer(
            retailer_id=self.retailer.retailer_id,
            full_name="Amina Hassan",
            phone_number="+254700000002",
        )
        db.session.add(self.customer)
        db.session.flush()
        self.customer_id = self.customer.customer_id
        self.retailer_id = self.retailer.retailer_id
        retailer_id = self.retailer_id

        @self.app.before_request
        def authenticate_test_retailer():
            login_user(db.session.get(Retailer, retailer_id))

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.app_context.pop()

    def _add_credit_sale(self, amount):
        db.session.add(
            CreditTransaction(
                retailer_id=self.retailer_id,
                customer_id=self.customer_id,
                item_description="Groceries",
                amount=amount,
            )
        )
        db.session.add(
            Ledger(
                retailer_id=self.retailer_id,
                customer_id=self.customer_id,
                outstanding_balance=amount,
            )
        )
        db.session.commit()

    def test_payment_form_rejects_missing_customer_and_invalid_amounts(self):
        with self.app.test_request_context():
            for amount in ("0", "1.001"):
                with self.subTest(amount=amount):
                    form = PaymentForm(
                        formdata=MultiDict({"customer_id": "", "amount_paid": amount})
                    )
                    form.customer_id.choices = [("", "Choose a customer"), (1, "Amina")]

                    self.assertFalse(form.validate())
                    self.assertIn("customer_id", form.errors)
                    self.assertIn("amount_paid", form.errors)

    def test_payment_cannot_exceed_current_outstanding_balance(self):
        self._add_credit_sale(100)

        response = self.client.post(
            "/payments/new",
            data={"customer_id": str(self.customer_id), "amount_paid": "100.01"},
        )

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Payment cannot exceed the outstanding balance", response.data)
        self.assertEqual(Payment.query.count(), 0)
        self.assertEqual(Ledger.query.one().outstanding_balance, 100)

    def test_valid_payment_updates_balance(self):
        self._add_credit_sale(100)

        response = self.client.post(
            "/payments/new",
            data={"customer_id": str(self.customer_id), "amount_paid": "40.00"},
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(Payment.query.count(), 1)
        self.assertEqual(Ledger.query.one().outstanding_balance, 60)

    def test_customer_without_debt_cannot_be_selected_for_payment(self):
        response = self.client.get("/payments/new")

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"No outstanding payments", response.data)
        self.assertNotIn(b"id=\"amount_paid\"", response.data)


if __name__ == "__main__":
    unittest.main()
