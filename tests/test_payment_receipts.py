import unittest
from datetime import datetime
from decimal import Decimal
from urllib.parse import unquote

from flask import g
from flask_login import login_user

from app import create_app
from app.extensions import db
from app.models import CreditTransaction, Customer, Ledger, Payment, Retailer, SaleItem, Shopkeeper
from app.payments.receipts import receipt_message


class ReceiptConfig:
    APP_ENV = "testing"
    SECRET_KEY = "test-secret"
    SQLALCHEMY_DATABASE_URI = "sqlite://"
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    SQLALCHEMY_ENGINE_OPTIONS = {}
    AUTO_CREATE_DB = False
    WTF_CSRF_ENABLED = False
    DEFAULT_PHONE_REGION = "KE"


class PaymentReceiptTests(unittest.TestCase):
    def setUp(self):
        self.app = create_app(ReceiptConfig)
        self.app_context = self.app.app_context()
        self.app_context.push()
        db.create_all()
        self.client = self.app.test_client()
        self.retailer = Retailer(business_name="Mama Njeri Shop", owner_name="Njeri",
                                 phone_number="+254700000081", password_hash="x")
        db.session.add(self.retailer)
        db.session.flush()
        self.customer = Customer(retailer_id=self.retailer.retailer_id, full_name="Amina Hassan",
                                 phone_number="+254712345678", preferred_channel="both")
        self.shopkeeper = Shopkeeper(retailer_id=self.retailer.retailer_id, full_name="Clerk",
                                     phone_number="+254700000082", password_hash="x")
        db.session.add_all([self.customer, self.shopkeeper])
        db.session.flush()
        sale = CreditTransaction(retailer_id=self.retailer.retailer_id, customer_id=self.customer.customer_id,
                                 item_description="Chips, Soda", amount=Decimal("400"),
                                 transaction_date=datetime(2026, 10, 20, 9, 0))
        sale.items = [SaleItem(position=0, description="Chips", amount=Decimal("100")),
                      SaleItem(position=1, description="Soda", amount=Decimal("300"))]
        db.session.add_all([sale, Ledger(retailer_id=self.retailer.retailer_id, customer_id=self.customer.customer_id)])
        db.session.commit()
        self.chips, self.soda = sale.items
        self.user = self.retailer

        @self.app.before_request
        def authenticate():
            g.pop("_login_user", None)
            login_user(self.user)

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.app_context.pop()

    def _pay(self, amount, items=(), method="cash", reference=""):
        return self.client.post("/payments/new", data={
            "customer_id": str(self.customer.customer_id), "amount_paid": amount,
            "payment_method": method, "payment_reference": reference,
            "sale_item_ids": [str(item.sale_item_id) for item in items],
        })

    def test_admin_goes_to_the_receipt_after_logging_a_payment(self):
        response = self._pay("300", [self.soda], method="mpesa_till", reference="QFT4ABC123")
        payment = Payment.query.one()

        self.assertTrue(response.headers["Location"].endswith(f"/payments/{payment.payment_id}/receipt"))
        page = self.client.get(response.headers["Location"]).get_data(as_text=True)
        self.assertIn("Send Amina Hassan a receipt", page)
        self.assertIn("Send on WhatsApp", page)
        self.assertIn("https://wa.me/254712345678?text=", page)
        self.assertIn("sms:+254712345678?body=", page)

    def test_message_has_amount_eat_time_method_items_and_balance(self):
        self._pay("300", [self.soda], method="mpesa_till", reference="QFT4ABC123")
        payment = Payment.query.one()
        payment.payment_date = datetime(2026, 10, 22, 7, 15)  # 10:15 EAT
        db.session.commit()

        message = receipt_message(payment)

        self.assertIn("Hello Amina Hassan, Mama Njeri Shop confirms we received your payment of KES 300.00", message)
        self.assertIn("on 22 Oct 2026 at 10:15 EAT", message)
        self.assertIn("Paid by M-Pesa Till (Buy Goods), ref QFT4ABC123.", message)
        self.assertIn("For: Soda KES 300.00.", message)
        self.assertIn("Balance still owed: KES 100.00.", message)

    def test_payment_without_chosen_items_lists_what_it_actually_paid(self):
        self._pay("150")

        message = receipt_message(Payment.query.one())

        self.assertIn("For: Chips KES 100.00, Soda KES 50.00 of 300.00.", message)
        self.assertIn("Balance still owed: KES 250.00.", message)

    def test_each_payment_lists_its_own_items(self):
        self._pay("100")
        self._pay("300", [self.soda])
        first, second = Payment.query.order_by(Payment.payment_id).all()

        self.assertIn("For: Chips KES 100.00.", receipt_message(first))
        self.assertIn("For: Soda KES 300.00.", receipt_message(second))
        self.assertIn("Your account is fully paid.", receipt_message(second))

    def test_receipt_names_the_units_paid_for(self):
        self.soda.quantity, self.soda.unit_price = 3, Decimal("100")
        db.session.commit()
        self.client.post("/payments/new", data={
            "customer_id": str(self.customer.customer_id), "amount_paid": "200", "payment_method": "cash",
            "sale_item_ids": [str(self.soda.sale_item_id)], f"units_{self.soda.sale_item_id}": "2",
        })

        message = receipt_message(Payment.query.one())

        self.assertIn("For: Soda ×2 of 3 KES 200.00 of 300.00.", message)

    def test_receipt_offers_only_the_customers_chosen_channel(self):
        self.customer.preferred_channel = "whatsapp"
        db.session.commit()
        response = self._pay("100")
        page = self.client.get(response.headers["Location"]).get_data(as_text=True)

        self.assertIn("Send on WhatsApp", page)
        self.assertNotIn("Send by SMS", page)

    def test_page_shows_the_customer_message_in_the_links(self):
        response = self._pay("300", [self.soda])
        page = self.client.get(response.headers["Location"]).get_data(as_text=True)

        whatsapp = page.split('https://wa.me/254712345678?text=')[1].split('"')[0]
        self.assertIn("For: Soda KES 300.00.", unquote(whatsapp))

    def test_shopkeeper_payments_wait_for_approval_before_a_receipt(self):
        self.user = self.shopkeeper
        response = self._pay("100", [self.chips])
        payment = Payment.query.one()

        self.assertTrue(response.headers["Location"].endswith("/payments/"))
        self.assertEqual(self.client.get(f"/payments/{payment.payment_id}/receipt").status_code, 403)

        self.user = self.retailer
        pending = self.client.get(f"/payments/{payment.payment_id}/receipt")
        self.assertTrue(pending.headers["Location"].endswith("/payments/"))
        approved = self.client.post(f"/payments/{payment.payment_id}/approve")
        self.assertTrue(approved.headers["Location"].endswith(f"/payments/{payment.payment_id}/receipt"))
        self.assertIn("For: Chips KES 100.00.", receipt_message(db.session.get(Payment, payment.payment_id)))

    def test_payment_history_links_to_receipts_for_confirmed_payments(self):
        self._pay("100")

        page = self.client.get("/payments/").get_data(as_text=True)

        self.assertIn(f"/payments/{Payment.query.one().payment_id}/receipt", page)
        self.assertIn("Send receipt", page)

    def test_other_shops_cannot_open_a_receipt(self):
        self._pay("100")
        other = Retailer(business_name="Other", owner_name="O", phone_number="+254700000089", password_hash="x")
        db.session.add(other)
        db.session.commit()
        self.user = other

        self.assertEqual(self.client.get(f"/payments/{Payment.query.one().payment_id}/receipt").status_code, 404)

    def test_viewing_a_receipt_changes_nothing(self):
        self._pay("100")
        payment = Payment.query.one()
        before = (Payment.query.count(), Ledger.query.one().outstanding_balance)

        self.client.get(f"/payments/{payment.payment_id}/receipt")

        self.assertEqual((Payment.query.count(), Ledger.query.one().outstanding_balance), before)


if __name__ == "__main__":
    unittest.main()
