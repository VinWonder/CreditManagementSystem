import unittest
from datetime import datetime
from decimal import Decimal
from unittest.mock import MagicMock, patch

from cryptography.fernet import Fernet
from flask import g
from flask_login import login_user

from app import create_app
from app.extensions import db
from app.models import (
    CreditTransaction,
    Customer,
    Ledger,
    MpesaAccount,
    Payment,
    PaymentAllocation,
    Retailer,
    SaleItem,
    Shopkeeper,
    StkPushRequest,
)
from app.mpesa import daraja
from app.reminders.credentials import encrypt_secret
from app.reminders.engine import unpaid_item_details
from app.transactions.items import customer_item_balances


class SaleItemConfig:
    APP_ENV = "testing"
    SECRET_KEY = "test-secret"
    SQLALCHEMY_DATABASE_URI = "sqlite://"
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    SQLALCHEMY_ENGINE_OPTIONS = {}
    AUTO_CREATE_DB = False
    WTF_CSRF_ENABLED = False
    DEFAULT_PHONE_REGION = "KE"
    MESSAGING_CREDENTIALS_ENCRYPTION_KEY = Fernet.generate_key().decode()
    PUBLIC_BASE_URL = "https://duka.example.com"


class SaleItemTests(unittest.TestCase):
    def setUp(self):
        self.app = create_app(SaleItemConfig)
        self.app_context = self.app.app_context()
        self.app_context.push()
        db.create_all()
        self.client = self.app.test_client()
        self.retailer = Retailer(business_name="Kiosk", owner_name="Owner",
                                 phone_number="+254700000061", password_hash="x")
        db.session.add(self.retailer)
        db.session.flush()
        self.customer = Customer(retailer_id=self.retailer.retailer_id, full_name="Brian Ouma",
                                 phone_number="+254712345678")
        self.other = Customer(retailer_id=self.retailer.retailer_id, full_name="Other Person",
                              phone_number="+254722000000")
        self.shopkeeper = Shopkeeper(retailer_id=self.retailer.retailer_id, full_name="Clerk",
                                     phone_number="+254700000062", password_hash="x")
        db.session.add_all([self.customer, self.other, self.shopkeeper])
        db.session.commit()
        self.user = self.retailer

        @self.app.before_request
        def authenticate():
            g.pop("_login_user", None)
            login_user(self.user)

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.app_context.pop()

    def _sell(self, items, customer=None):
        data = {"customer_id": str((customer or self.customer).customer_id)}
        for index, item in enumerate(items):
            description, quantity, price = item if len(item) == 3 else (item[0], "1", item[1])
            data[f"items-{index}-item_description"] = description
            data[f"items-{index}-quantity"] = quantity
            data[f"items-{index}-unit_price"] = price
        return self.client.post("/transactions/new", data=data)

    def _pay(self, amount, item_ids=(), method="cash", customer=None, units=None, **extra):
        data = {
            "customer_id": str((customer or self.customer).customer_id),
            "amount_paid": amount,
            "payment_method": method,
            "sale_item_ids": [str(item_id) for item_id in item_ids],
            **extra,
        }
        for item_id, count in (units or {}).items():
            data[f"units_{item_id}"] = str(count)
        return self.client.post("/payments/new", data=data)

    def _items(self):
        return {item.description: item for item in SaleItem.query.all()}

    def _status(self):
        return {b.description: (b.remaining, b.status) for b in customer_item_balances(self.customer)}

    def _balance(self):
        return Ledger.query.filter_by(customer_id=self.customer.customer_id).one().outstanding_balance

    # Recording

    def test_several_items_are_one_sale_with_a_total(self):
        response = self._sell([("Chips", "100"), ("Soda", "300")])

        self.assertEqual(response.status_code, 302)
        sale = CreditTransaction.query.one()
        self.assertEqual(sale.amount, Decimal("400.00"))
        self.assertEqual(sale.item_description, "Chips, Soda")
        self.assertEqual([(i.description, i.amount) for i in sale.items],
                         [("Chips", Decimal("100.00")), ("Soda", Decimal("300.00"))])
        self.assertEqual(self._balance(), Decimal("400.00"))

    def test_every_item_needs_a_name_and_price(self):
        response = self._sell([("Chips", "100"), ("", "300"), ("Bread", "")])
        page = response.get_data(as_text=True)

        self.assertEqual(response.status_code, 200)
        self.assertIn("Enter what the customer took.", page)
        self.assertIn("Enter the price of one.", page)
        self.assertEqual(CreditTransaction.query.count(), 0)

    def test_sale_page_has_item_rows_and_a_total(self):
        page = self.client.get("/transactions/new").get_data(as_text=True)

        self.assertIn('name="items-0-item_description"', page)
        self.assertIn("Add another item", page)
        self.assertIn('id="sale-items-total"', page)

    def test_offline_sale_with_several_items_syncs_as_one_sale(self):
        response = self.client.post("/transactions/api/sync", json={"transactions": [{
            "client_id": "local-1",
            "customer_id": self.customer.customer_id,
            "items": [
                {"item_description": "Chips", "item_size_inches": "", "amount": "100.00"},
                {"item_description": "Pipe", "item_size_inches": "1/2", "amount": "250.00"},
            ],
        }]})

        self.assertEqual(response.get_json()["results"][0]["status"], "synced")
        sale = CreditTransaction.query.one()
        self.assertEqual(sale.amount, Decimal("350.00"))
        self.assertEqual([i.description for i in sale.items], ["Chips", "Pipe (1/2 in)"])

    def test_offline_sale_with_a_bad_item_is_refused(self):
        response = self.client.post("/transactions/api/sync", json={"transactions": [{
            "customer_id": self.customer.customer_id,
            "items": [{"item_description": "Chips", "amount": "100"}, {"item_description": "Soda", "amount": "0"}],
        }]})

        self.assertEqual(response.get_json()["results"][0]["status"], "failed")
        self.assertEqual(CreditTransaction.query.count(), 0)

    # Paying for items

    def test_paying_for_one_item_leaves_the_other_due(self):
        self._sell([("Chips", "100"), ("Soda", "300")])

        self._pay("300", [self._items()["Soda"].sale_item_id])

        self.assertEqual(self._status(), {"Chips": (Decimal("100.00"), "unpaid"),
                                          "Soda": (Decimal("0.00"), "paid")})
        self.assertEqual(self._balance(), Decimal("100.00"))
        page = self.client.get(f"/customers/{self.customer.customer_id}").get_data(as_text=True)
        self.assertIn("KES 100.00 due", page)
        self.assertIn("Paid", page)
        self.assertIn("For Soda", self.client.get("/payments/").get_data(as_text=True))

    def test_payment_without_items_pays_the_oldest_first(self):
        self._sell([("Chips", "100"), ("Soda", "300")])

        self._pay("150")

        self.assertEqual(self._status(), {"Chips": (Decimal("0.00"), "paid"),
                                          "Soda": (Decimal("250.00"), "partly paid")})
        self.assertEqual(PaymentAllocation.query.count(), 0)

    def test_amount_cannot_exceed_what_the_chosen_items_owe(self):
        self._sell([("Chips", "100"), ("Soda", "300")])

        response = self._pay("150", [self._items()["Chips"].sale_item_id])

        self.assertIn(b"The chosen items only have KES 100.00 left to pay", response.data)
        self.assertEqual(Payment.query.count(), 0)

    def test_another_customers_item_cannot_be_chosen(self):
        self._sell([("Chips", "100")])
        self._sell([("Milk", "80")], customer=self.other)

        response = self._pay("80", [self._items()["Milk"].sale_item_id])

        self.assertIn(b"One of the chosen items is not owed by this customer", response.data)
        self.assertEqual(Payment.query.count(), 0)

    def test_payment_page_offers_only_unpaid_items(self):
        self._sell([("Chips", "100"), ("Soda", "300")])
        self._pay("300", [self._items()["Soda"].sale_item_id])

        page = self.client.get("/payments/new").get_data(as_text=True)

        self.assertIn("Chips", page)
        self.assertNotIn('"Soda', page)
        self.assertIn("What is this payment for?", page)

    def test_item_under_review_is_not_offered_twice(self):
        self._sell([("Chips", "100"), ("Soda", "300")])
        self.user = self.shopkeeper
        self._pay("300", [self._items()["Soda"].sale_item_id])

        self.assertEqual(Payment.query.one().approval_status, "pending")
        self.assertEqual(self._status()["Soda"], (Decimal("300.00"), "unpaid"))
        page = self.client.get("/payments/new").get_data(as_text=True)
        self.assertNotIn("\"Soda", page)
        self.assertIn("\"Chips", page)

        self.user = self.retailer
        self.client.post(f"/payments/{Payment.query.one().payment_id}/approve")
        self.assertEqual(self._status()["Soda"], (Decimal("0.00"), "paid"))

    # Reminders

    def test_reminder_lists_only_what_is_still_owed(self):
        self._sell([("Chips", "100"), ("Soda", "300"), ("Bread", "60")])
        items = self._items()
        self._pay("300", [items["Soda"].sale_item_id])
        self._pay("40", [items["Bread"].sale_item_id])
        self._sell([("Sugar", "200")])

        details = unpaid_item_details(self.customer)

        self.assertTrue(details.startswith("Chips KES 100.00, Bread (KES 20.00 left) (taken "), details)
        self.assertNotIn("Soda", details)
        self.assertIn("; Sugar (taken ", details)

    def test_old_sales_without_item_rows_still_work(self):
        db.session.add(CreditTransaction(retailer_id=self.retailer.retailer_id, customer_id=self.customer.customer_id,
                                         item_description="Older purchase", amount=Decimal("50"),
                                         transaction_date=datetime(2026, 9, 1)))
        db.session.add(Payment(retailer_id=self.retailer.retailer_id, customer_id=self.customer.customer_id,
                               amount_paid=Decimal("20"), approval_status="approved"))
        db.session.commit()

        self.assertEqual(self._status(), {"Older purchase": (Decimal("30.00"), "partly paid")})

    # M-Pesa prompts

    def test_prompt_for_chosen_items_applies_them_when_paid(self):
        db.session.add(MpesaAccount(retailer_id=self.retailer.retailer_id, environment="sandbox",
                                    account_type="paybill", shortcode="174379",
                                    consumer_key_encrypted=encrypt_secret("k"),
                                    consumer_secret_encrypted=encrypt_secret("s"),
                                    passkey_encrypted=encrypt_secret("p")))
        db.session.commit()
        self._sell([("Chips", "100"), ("Soda", "300")])
        daraja._token_cache.clear()
        self.addCleanup(daraja._token_cache.clear)
        token = MagicMock(status_code=200)
        token.json.return_value = {"access_token": "t", "expires_in": "3599"}
        accepted = MagicMock(status_code=200)
        accepted.json.return_value = {"ResponseCode": "0", "CheckoutRequestID": "ws_CO_1", "MerchantRequestID": "m1"}

        with patch("app.mpesa.daraja.requests") as fake:
            fake.RequestException = Exception
            fake.get.return_value = token
            fake.post.return_value = accepted
            self.client.post("/payments/new", data={
                "customer_id": str(self.customer.customer_id), "amount_paid": "300",
                "payment_method": "mpesa_stk", "stk_phone": "0712345678",
                "sale_item_ids": [str(self._items()["Soda"].sale_item_id)],
            })
        stk = StkPushRequest.query.one()
        self.client.post(f"/mpesa/callback/{stk.callback_token}", json={"Body": {"stkCallback": {
            "CheckoutRequestID": "ws_CO_1", "ResultCode": 0, "ResultDesc": "ok",
            "CallbackMetadata": {"Item": [{"Name": "Amount", "Value": 300},
                                          {"Name": "MpesaReceiptNumber", "Value": "QFT4ABC123"}]}}}})

        self.assertEqual(self._status(), {"Chips": (Decimal("100.00"), "unpaid"),
                                          "Soda": (Decimal("0.00"), "paid")})

    # Quantities

    def test_quantity_times_unit_price_makes_the_line_total(self):
        self._sell([("Soda", "3", "100"), ("Chips", "1", "50")])

        sale = CreditTransaction.query.one()
        self.assertEqual(sale.amount, Decimal("350.00"))
        self.assertEqual(sale.item_description, "Soda ×3, Chips")
        soda = self._items()["Soda"]
        self.assertEqual((soda.quantity, soda.unit_price, soda.amount), (3, Decimal("100.00"), Decimal("300.00")))

    def test_quantity_must_be_a_whole_number_of_at_least_one(self):
        for quantity in ("0", "-2", "1.5", ""):
            response = self._sell([("Soda", quantity, "100")])
            self.assertEqual(response.status_code, 200, quantity)
        self.assertEqual(CreditTransaction.query.count(), 0)

    def test_sale_page_asks_for_quantity_and_price_of_one(self):
        page = self.client.get("/transactions/new").get_data(as_text=True)

        self.assertIn('name="items-0-quantity"', page)
        self.assertIn('name="items-0-unit_price"', page)

    def test_offline_sale_keeps_quantities_and_old_entries_still_sync(self):
        response = self.client.post("/transactions/api/sync", json={"transactions": [
            {"client_id": "local-q", "customer_id": self.customer.customer_id,
             "items": [{"item_description": "Soda", "quantity": 4, "unit_price": "60.00"}]},
            {"client_id": "local-old", "customer_id": self.customer.customer_id,
             "items": [{"item_description": "Chips", "amount": "100.00"}]},
        ]})

        self.assertEqual([r["status"] for r in response.get_json()["results"]], ["synced", "synced"])
        items = self._items()
        self.assertEqual((items["Soda"].quantity, items["Soda"].amount), (4, Decimal("240.00")))
        self.assertEqual((items["Chips"].quantity, items["Chips"].amount), (1, Decimal("100.00")))

    def test_paying_for_two_of_three_units(self):
        self._sell([("Soda", "3", "100")])
        soda = self._items()["Soda"]

        self._pay("200", [soda.sale_item_id], units={soda.sale_item_id: 2})

        allocation = PaymentAllocation.query.one()
        self.assertEqual((allocation.amount, allocation.quantity, allocation.label),
                         (Decimal("200.00"), 2, "Soda ×2"))
        balance = customer_item_balances(self.customer)[0]
        self.assertEqual((balance.remaining, balance.units_left), (Decimal("100.00"), 1))
        self.assertEqual(self._balance(), Decimal("100.00"))

    def test_amount_cannot_exceed_the_chosen_units(self):
        self._sell([("Soda", "3", "100")])
        soda = self._items()["Soda"]

        response = self._pay("300", [soda.sale_item_id], units={soda.sale_item_id: 2})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(Payment.query.count(), 0)

    def test_cannot_pay_for_more_units_than_are_left(self):
        self._sell([("Soda", "3", "100")])
        soda = self._items()["Soda"]
        self._pay("200", [soda.sale_item_id], units={soda.sale_item_id: 2})

        response = self._pay("200", [soda.sale_item_id], units={soda.sale_item_id: 2})

        self.assertEqual(response.status_code, 200)
        self.assertEqual(Payment.query.count(), 1)

    def test_payment_page_offers_units_for_items_bought_in_numbers(self):
        self._sell([("Soda", "3", "100"), ("Chips", "1", "50")])

        page = self.client.get(f"/payments/new?customer_id={self.customer.customer_id}").get_data(as_text=True)

        self.assertIn('"units_left": 3', page)
        self.assertIn('units_', page)

    def test_reminder_and_customer_page_show_quantities(self):
        self._sell([("Soda", "3", "100")])

        self.assertIn("Soda ×3 KES 300.00", unpaid_item_details(self.customer))
        page = self.client.get(f"/customers/{self.customer.customer_id}").get_data(as_text=True)
        self.assertIn("Soda ×3", page)
        self.assertIn("3 × KES 100.00", page)

    def test_prompt_for_some_units_applies_them_when_paid(self):
        db.session.add(MpesaAccount(retailer_id=self.retailer.retailer_id, environment="sandbox",
                                    account_type="paybill", shortcode="174379",
                                    consumer_key_encrypted=encrypt_secret("k"),
                                    consumer_secret_encrypted=encrypt_secret("s"),
                                    passkey_encrypted=encrypt_secret("p")))
        db.session.commit()
        self._sell([("Soda", "3", "100")])
        soda = self._items()["Soda"]
        daraja._token_cache.clear()
        self.addCleanup(daraja._token_cache.clear)
        token = MagicMock(status_code=200)
        token.json.return_value = {"access_token": "t", "expires_in": "3599"}
        accepted = MagicMock(status_code=200)
        accepted.json.return_value = {"ResponseCode": "0", "CheckoutRequestID": "ws_CO_2", "MerchantRequestID": "m2"}

        with patch("app.mpesa.daraja.requests") as fake:
            fake.RequestException = Exception
            fake.get.return_value = token
            fake.post.return_value = accepted
            self._pay("100", [soda.sale_item_id], method="mpesa_stk", units={soda.sale_item_id: 1},
                      stk_phone="0712345678")
        stk = StkPushRequest.query.one()
        self.assertEqual(stk.sale_item_ids, f"{soda.sale_item_id}:1")
        self.client.post(f"/mpesa/callback/{stk.callback_token}", json={"Body": {"stkCallback": {
            "CheckoutRequestID": "ws_CO_2", "ResultCode": 0, "ResultDesc": "ok",
            "CallbackMetadata": {"Item": [{"Name": "Amount", "Value": 100},
                                          {"Name": "MpesaReceiptNumber", "Value": "QFT4ABC124"}]}}}})

        self.assertEqual(PaymentAllocation.query.one().quantity, 1)
        self.assertEqual(customer_item_balances(self.customer)[0].units_left, 2)


if __name__ == "__main__":
    unittest.main()
