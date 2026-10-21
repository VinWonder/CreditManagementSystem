import unittest
from datetime import datetime
from decimal import Decimal

from flask import g
from flask_login import login_user

from app import create_app
from app.extensions import db
from app.models import CreditTransaction, Customer, Ledger, Payment, Retailer, Shopkeeper


class PaymentMethodConfig:
    APP_ENV = "testing"
    SECRET_KEY = "test-secret"
    SQLALCHEMY_DATABASE_URI = "sqlite://"
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    SQLALCHEMY_ENGINE_OPTIONS = {}
    AUTO_CREATE_DB = False
    WTF_CSRF_ENABLED = False
    DEFAULT_PHONE_REGION = "KE"


class PaymentMethodTests(unittest.TestCase):
    def setUp(self):
        self.app = create_app(PaymentMethodConfig)
        self.app_context = self.app.app_context()
        self.app_context.push()
        db.create_all()
        self.client = self.app.test_client()

        self.retailer = Retailer(
            business_name="Method Shop",
            owner_name="Method Owner",
            phone_number="+254700000051",
            password_hash="test-hash",
        )
        db.session.add(self.retailer)
        db.session.flush()
        self.customer = Customer(
            retailer_id=self.retailer.retailer_id,
            full_name="Grace Achieng",
            phone_number="+254700000052",
        )
        db.session.add(self.customer)
        db.session.flush()
        self.shopkeeper = Shopkeeper(
            retailer_id=self.retailer.retailer_id,
            full_name="Till Clerk",
            phone_number="+254700000053",
            password_hash="test-hash",
        )
        db.session.add_all(
            [
                self.shopkeeper,
                CreditTransaction(
                    retailer_id=self.retailer.retailer_id,
                    customer_id=self.customer.customer_id,
                    item_description="Flour",
                    amount=Decimal("1000.00"),
                    transaction_date=datetime(2026, 10, 1),
                ),
                Ledger(
                    retailer_id=self.retailer.retailer_id,
                    customer_id=self.customer.customer_id,
                    outstanding_balance=Decimal("1000.00"),
                ),
            ]
        )
        db.session.commit()

        self.user = self.shopkeeper

        @self.app.before_request
        def authenticate():
            g.pop("_login_user", None)
            login_user(self.user)

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.app_context.pop()

    def _pay(self, method, reference="", amount="100.00"):
        return self.client.post(
            "/payments/new",
            data={
                "customer_id": str(self.customer.customer_id),
                "amount_paid": amount,
                "payment_method": method,
                "payment_reference": reference,
            },
        )

    def test_payment_form_lists_every_method(self):
        html = self.client.get("/payments/new").get_data(as_text=True)

        for label in (
            "Cash",
            "Bank transfer / deposit",
            "Agent withdrawal",
            "M-Pesa Send Money",
            "M-Pesa Till (Buy Goods)",
            "M-Pesa Paybill",
            "Cheque",
        ):
            self.assertIn(label, html)

    def test_shopkeeper_mpesa_payment_waits_for_approval_with_its_code(self):
        response = self._pay("mpesa_till", " qft4abc123 ")

        self.assertEqual(response.status_code, 302)
        payment = Payment.query.one()
        self.assertEqual(payment.approval_status, "pending")
        self.assertEqual(payment.payment_method, "mpesa_till")
        self.assertEqual(payment.payment_reference, "QFT4ABC123")
        self.assertEqual(Ledger.query.one().outstanding_balance, Decimal("1000.00"))

    def test_admin_chooses_a_method_and_the_payment_is_approved(self):
        self.user = self.retailer

        self._pay("mpesa_paybill", "RKL9XYZ456")

        payment = Payment.query.one()
        self.assertEqual(payment.approval_status, "approved")
        self.assertEqual(payment.payment_method, "mpesa_paybill")
        self.assertEqual(Ledger.query.one().outstanding_balance, Decimal("900.00"))

    def test_method_is_required(self):
        response = self._pay("")

        self.assertIn(b"Choose how the customer paid.", response.data)
        self.assertEqual(Payment.query.count(), 0)

    def test_unknown_method_is_rejected(self):
        self._pay("bitcoin")

        self.assertEqual(Payment.query.count(), 0)

    def test_mpesa_transaction_code_is_optional(self):
        for method in ("agent", "mpesa_send", "mpesa_till", "mpesa_paybill"):
            with self.subTest(method=method):
                response = self._pay(method, "", amount="10.00")
                self.assertEqual(response.status_code, 302)
        payments = Payment.query.all()
        self.assertEqual(len(payments), 4)
        self.assertTrue(all(payment.payment_reference is None for payment in payments))

    def test_a_given_mpesa_code_must_look_like_one(self):
        for method in ("agent", "mpesa_send", "mpesa_till", "mpesa_paybill"):
            with self.subTest(method=method):
                malformed = self._pay(method, "AB-1")
                self.assertIn(b"M-Pesa codes are 8-12 letters and numbers", malformed.data)
        self.assertEqual(Payment.query.count(), 0)

    def test_cheque_needs_a_cheque_number(self):
        response = self._pay("cheque", "")

        self.assertIn(b"Enter the cheque number.", response.data)
        self._pay("cheque", "000123")
        self.assertEqual(Payment.query.one().payment_reference, "000123")

    def test_bank_reference_is_optional(self):
        self._pay("bank", "")
        self._pay("bank", "FT2629401234")

        references = sorted(
            (payment.payment_reference or "") for payment in Payment.query.all()
        )
        self.assertEqual(references, ["", "FT2629401234"])

    def test_cash_does_not_keep_a_reference(self):
        self._pay("cash", "SOMETHING")

        payment = Payment.query.one()
        self.assertEqual(payment.payment_method, "cash")
        self.assertIsNone(payment.payment_reference)

    def test_admin_sees_method_and_reference_before_approving(self):
        self._pay("mpesa_send", "QFT4ABC123")
        self.user = self.retailer

        history = self.client.get("/payments/").get_data(as_text=True)
        dashboard = self.client.get("/dashboard").get_data(as_text=True)
        portfolio = self.client.get(f"/customers/{self.customer.customer_id}").get_data(as_text=True)

        for page in (history, dashboard, portfolio):
            self.assertIn("M-Pesa Send Money", page)
            self.assertIn("Ref QFT4ABC123", page)

    def test_payments_from_before_methods_show_not_recorded(self):
        db.session.add(
            Payment(
                retailer_id=self.retailer.retailer_id,
                customer_id=self.customer.customer_id,
                amount_paid=Decimal("50.00"),
            )
        )
        db.session.commit()
        self.user = self.retailer

        history = self.client.get("/payments/").get_data(as_text=True)

        self.assertIn("Not recorded", history)
        self.assertIn("payment-method is-unrecorded", history)


if __name__ == "__main__":
    unittest.main()
