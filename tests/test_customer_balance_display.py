import unittest
from types import SimpleNamespace
from unittest.mock import patch

from app.customers.routes import _customer_ids_with_credit_sales


class CustomerPurchaseDisplayTests(unittest.TestCase):
    @patch("app.customers.routes.db.session.query")
    def test_returns_customers_with_at_least_one_credit_sale(self, query):
        query.return_value.filter.return_value.distinct.return_value.all.return_value = [
            (2,),
            (5,),
        ]
        customers = [
            SimpleNamespace(customer_id=2),
            SimpleNamespace(customer_id=5),
            SimpleNamespace(customer_id=8),
        ]

        self.assertEqual(_customer_ids_with_credit_sales(customers), {2, 5})
        query.assert_called_once()

    @patch("app.customers.routes.db.session.query")
    def test_empty_customer_list_does_not_query_database(self, query):
        self.assertEqual(_customer_ids_with_credit_sales([]), set())
        query.assert_not_called()


if __name__ == "__main__":
    unittest.main()
