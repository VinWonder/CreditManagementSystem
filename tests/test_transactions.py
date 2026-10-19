import unittest
from decimal import Decimal
from datetime import datetime, timedelta
from werkzeug.datastructures import MultiDict
from flask import Flask
from flask_login import login_user

from app import create_app
from app.extensions import db
from app.models import CreditTransaction, Customer, Retailer
from app.transactions.forms import ITEM_SIZE_CHOICES, TransactionForm
from app.transactions.routes import _normalize_offline_transaction


class TransactionPageConfig:
    APP_ENV = "testing"
    SECRET_KEY = "test-secret"
    SQLALCHEMY_DATABASE_URI = "sqlite://"
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    SQLALCHEMY_ENGINE_OPTIONS = {}
    AUTO_CREATE_DB = False
    SCHEDULER_ENABLED = False
    WTF_CSRF_ENABLED = False


class TransactionValidationTests(unittest.TestCase):
    def setUp(self):
        self.app = Flask(__name__)
        self.app.config.update(SECRET_KEY="test-secret", WTF_CSRF_ENABLED=False)

    def _form(
        self, *, customer_id="1", description=" Rice ", amount="0.01", size_inches=""
    ):
        form = TransactionForm(
            formdata=MultiDict(
                {
                    "customer_id": customer_id,
                    "item_description": description,
                    "item_size_inches": size_inches,
                    "amount": amount,
                }
            )
        )
        form.customer_id.choices = [("", "Choose a customer"), (1, "Amina Hassan")]
        return form

    def test_form_accepts_minimum_positive_amount_and_trims_description(self):
        with self.app.test_request_context():
            form = self._form()

            self.assertTrue(form.validate(), form.errors)
            self.assertEqual(form.amount.data, Decimal("0.01"))
            self.assertEqual(form.item_description.data, "Rice")

    def test_form_rejects_zero_amount_and_missing_customer(self):
        with self.app.test_request_context():
            form = self._form(customer_id="", amount="0")

            self.assertFalse(form.validate())
            self.assertIn("customer_id", form.errors)
            self.assertIn("amount", form.errors)

    def test_form_rejects_amounts_with_more_than_two_decimal_places(self):
        with self.app.test_request_context():
            form = self._form(amount="1.001")

            self.assertFalse(form.validate())
            self.assertIn("2 decimal places", form.amount.errors[0])

    def test_item_size_options_include_requested_fractional_and_whole_inches(self):
        with self.app.test_request_context():
            form = self._form()

            self.assertEqual(
                [value for value, _label in form.item_size_inches.choices[1:]],
                [
                    "1/2", "1/4", "3/4", "1", "1 1/4", "1 1/2", "2", "2 1/2",
                    "3", "4", "6", "8", "12", "16", "18",
                ],
            )
            self.assertEqual(len(ITEM_SIZE_CHOICES), 15)

    def test_form_accepts_size_and_rejects_size_that_exceeds_description_limit(self):
        with self.app.test_request_context():
            sized_form = self._form(size_inches="1 1/4")
            oversized_form = self._form(description="R" * 145, size_inches="1/2")

            self.assertTrue(sized_form.validate(), sized_form.errors)
            self.assertFalse(oversized_form.validate())
            self.assertIn("item_size_inches", oversized_form.errors)

    def test_offline_transaction_normalizes_valid_values(self):
        transaction, error = _normalize_offline_transaction(
            {"customer_id": 3, "item_description": "  Maize flour  ", "amount": "125.50"}
        )

        self.assertIsNone(error)
        self.assertEqual(transaction["customer_id"], 3)
        self.assertEqual(transaction["item_description"], "Maize flour")
        self.assertEqual(transaction["amount"], Decimal("125.50"))

    def test_offline_transaction_includes_selected_size_and_rejects_unknown_size(self):
        transaction, error = _normalize_offline_transaction(
            {
                "customer_id": 3,
                "item_description": "PVC pipe",
                "item_size_inches": "1 1/2",
                "amount": "125.50",
            }
        )

        self.assertIsNone(error)
        self.assertEqual(transaction["item_description"], "PVC pipe (1 1/2 in)")

        invalid_transaction, error = _normalize_offline_transaction(
            {
                "customer_id": 3,
                "item_description": "PVC pipe",
                "item_size_inches": "1/3",
                "amount": "125.50",
            }
        )
        self.assertIsNone(invalid_transaction)
        self.assertIn("valid item size", error)

    def test_offline_transaction_rejects_invalid_amount_or_description(self):
        cases = [
            ({"customer_id": 1, "item_description": "Rice", "amount": "0"}, "amount"),
            ({"customer_id": 1, "item_description": "Rice", "amount": "1.001"}, "amount"),
            ({"customer_id": 1, "item_description": " ", "amount": "1"}, "description"),
            ({"customer_id": 1, "item_description": "R" * 151, "amount": "1"}, "description"),
        ]
        for item, expected in cases:
            with self.subTest(item=item):
                transaction, error = _normalize_offline_transaction(item)

                self.assertIsNone(transaction)
                self.assertIn(expected, error)


class TransactionHistoryPageTests(unittest.TestCase):
    def setUp(self):
        self.app = create_app(TransactionPageConfig)
        self.app_context = self.app.app_context()
        self.app_context.push()
        db.create_all()
        self.client = self.app.test_client()

        self.retailer = Retailer(
                business_name="Test Shop",
                owner_name="Test Owner",
                phone_number="+254700000031",
                password_hash="test-hash",
        )
        db.session.add(self.retailer)
        db.session.flush()
        self.retailer_id = self.retailer.retailer_id
        self.customer = self._add_customer("Amina Hassan", "+254700000032")
        self.other_customer = self._add_customer("Brian Otieno", "+254700000033")
        retailer_id = self.retailer_id

        @self.app.before_request
        def authenticate_test_retailer():
                login_user(db.session.get(Retailer, retailer_id))

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.app_context.pop()

    def _add_customer(self, name, phone, retailer_id=None):
        customer = Customer(
                retailer_id=retailer_id or self.retailer_id,
                full_name=name,
                phone_number=phone,
        )
        db.session.add(customer)
        db.session.flush()
        return customer

    def _add_transaction(
        self, customer, description, amount, *, status="synced", transaction_date=None
    ):
        transaction = CreditTransaction(
                retailer_id=customer.retailer_id,
                customer_id=customer.customer_id,
                item_description=description,
                amount=amount,
                sync_status=status,
                transaction_date=transaction_date or datetime(2026, 10, 14, 8, 0),
        )
        db.session.add(transaction)
        db.session.commit()
        return transaction

    def test_empty_history_shows_first_sale_action(self):
        response = self.client.get("/transactions/")

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"No credit sales yet", response.data)
        self.assertIn(b"Record your first sale", response.data)
        self.assertIn(b"KES 0.00", response.data)

    def test_credit_sale_form_offers_sizes_and_saves_size_with_description(self):
        form_response = self.client.get("/transactions/new")
        self.assertEqual(form_response.status_code, 200)
        self.assertIn(b'1 1/4"', form_response.data)
        self.assertIn(b'18"', form_response.data)

        response = self.client.post(
            "/transactions/new",
            data={
                "customer_id": str(self.customer.customer_id),
                "item_description": "PVC pipe",
                "item_size_inches": "1/2",
                "amount": "125.00",
            },
        )

        self.assertEqual(response.status_code, 302)
        transaction = CreditTransaction.query.one()
        self.assertEqual(transaction.item_description, "PVC pipe (1/2 in)")

    def test_history_summaries_and_sync_labels_are_clear(self):
        self._add_transaction(self.customer, "Rice", "120.50")
        self._add_transaction(self.customer, "Cooking oil", "30.00", status="pending")

        response = self.client.get("/transactions/")

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"KES 150.50", response.data)
        self.assertIn(b"Sales recorded", response.data)
        self.assertIn(b"Awaiting sync", response.data)
        self.assertIn(b"Amina Hassan", response.data)
        self.assertIn(b"Synced", response.data)
        self.assertIn(b"Awaiting sync", response.data)
        self.assertIn(b"11:00 EAT", response.data)

    def test_search_matches_customer_or_item_and_sync_filter(self):
        self._add_transaction(self.customer, "Rice", "120.00")
        self._add_transaction(self.customer, "Wheat flour", "40.00", status="pending")
        self._add_transaction(self.other_customer, "Tea leaves", "55.00")

        response = self.client.get("/transactions/?q=flour&status=pending")

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Wheat flour", response.data)
        self.assertNotIn(b"Rice", response.data)
        self.assertNotIn(b"Tea leaves", response.data)
        self.assertIn(b'value="flour"', response.data)

        customer_search = self.client.get("/transactions/?q=Brian&status=synced")
        self.assertEqual(customer_search.status_code, 200)
        self.assertIn(b"Tea leaves", customer_search.data)
        self.assertNotIn(b"Wheat flour", customer_search.data)

    def test_history_is_scoped_to_signed_in_retailer(self):
        self._add_transaction(self.customer, "My sale", "25.00")
        other_retailer = Retailer(
                business_name="Other Shop",
                owner_name="Other Owner",
                phone_number="+254700000034",
                password_hash="test-hash",
        )
        db.session.add(other_retailer)
        db.session.flush()
        other_customer = self._add_customer(
                "Private Customer", "+254700000035", other_retailer.retailer_id
        )
        self._add_transaction(other_customer, "Private sale", "999.00")

        response = self.client.get("/transactions/")

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"My sale", response.data)
        self.assertNotIn(b"Private Customer", response.data)
        self.assertNotIn(b"Private sale", response.data)
        self.assertIn(b"KES 25.00", response.data)
        self.assertNotIn(b"KES 1,024.00", response.data)

    def test_history_paginates_and_preserves_search_and_status(self):
        start = datetime(2026, 10, 1)
        for index in range(26):
                self._add_transaction(
                    self.customer,
                    f"Item {index:02}",
                    "1.00",
                    transaction_date=start + timedelta(minutes=index),
                )

        first_page = self.client.get("/transactions/?q=Item&status=synced")
        self.assertEqual(first_page.status_code, 200)
        self.assertIn(b"Item 25", first_page.data)
        self.assertNotIn(b"Item 00", first_page.data)
        self.assertIn(b"q=Item&amp;status=synced&amp;page=2", first_page.data)

        second_page = self.client.get("/transactions/?q=Item&status=synced&page=2")
        self.assertEqual(second_page.status_code, 200)
        self.assertIn(b"Item 00", second_page.data)
        self.assertIn(b"Page 2 of 2", second_page.data)

    def test_print_view_includes_all_matching_sales_across_pages(self):
        start = datetime(2026, 10, 1)
        for index in range(26):
            self._add_transaction(
                self.customer,
                f"Item {index:02}",
                "1.00",
                transaction_date=start + timedelta(minutes=index),
            )
        self._add_transaction(self.other_customer, "Filtered item", "15.00", status="pending")

        response = self.client.get("/transactions/?q=Item&status=synced&print=1")

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Credit sales report", response.data)
        self.assertIn(b"Test Shop", response.data)
        self.assertIn(b"Total KES 26.00", response.data)
        self.assertIn(b"Item 00", response.data)
        self.assertIn(b"Item 25", response.data)
        self.assertNotIn(b"Filtered item", response.data)
        self.assertNotIn(b"Page 1 of 2", response.data)
        self.assertIn(b"window.print()", response.data)


if __name__ == "__main__":
    unittest.main()
