import unittest

from app.models import Retailer


class RetailerPasswordTests(unittest.TestCase):
    def test_set_password_stores_a_hash(self):
        retailer = Retailer()

        retailer.set_password("correct-horse-battery-staple")

        self.assertNotEqual(
            retailer.password_hash, "correct-horse-battery-staple"
        )

    def test_check_password_accepts_the_original_password(self):
        retailer = Retailer()
        retailer.set_password("correct-horse-battery-staple")

        self.assertTrue(retailer.check_password("correct-horse-battery-staple"))

    def test_check_password_rejects_a_different_password(self):
        retailer = Retailer()
        retailer.set_password("correct-horse-battery-staple")

        self.assertFalse(retailer.check_password("incorrect-password"))


if __name__ == "__main__":
    unittest.main()
