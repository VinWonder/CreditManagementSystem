import unittest
from decimal import Decimal
from werkzeug.datastructures import MultiDict
from flask import Flask

from app.transactions.forms import TransactionForm
from app.transactions.routes import _normalize_offline_transaction


class TransactionValidationTests(unittest.TestCase):
    def setUp(self):
        self.app = Flask(__name__)
        self.app.config.update(SECRET_KEY="test-secret", WTF_CSRF_ENABLED=False)

    def _form(self, *, customer_id="1", description=" Rice ", amount="0.01"):
        form = TransactionForm(
            formdata=MultiDict(
                {
                    "customer_id": customer_id,
                    "item_description": description,
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

    def test_offline_transaction_normalizes_valid_values(self):
        transaction, error = _normalize_offline_transaction(
            {"customer_id": 3, "item_description": "  Maize flour  ", "amount": "125.50"}
        )

        self.assertIsNone(error)
        self.assertEqual(transaction["customer_id"], 3)
        self.assertEqual(transaction["item_description"], "Maize flour")
        self.assertEqual(transaction["amount"], Decimal("125.50"))

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


if __name__ == "__main__":
    unittest.main()
