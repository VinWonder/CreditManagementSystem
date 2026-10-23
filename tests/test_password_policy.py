import unittest
from types import SimpleNamespace

from wtforms.validators import ValidationError

from app import create_app
from app.auth.forms import strong_password
from app.extensions import db
from app.models import Retailer


class PasswordPolicyConfig:
    APP_ENV = "testing"
    SECRET_KEY = "test-secret"
    SQLALCHEMY_DATABASE_URI = "sqlite://"
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    SQLALCHEMY_ENGINE_OPTIONS = {}
    AUTO_CREATE_DB = False
    WTF_CSRF_ENABLED = False
    DEFAULT_PHONE_REGION = "KE"


def check(value):
    strong_password(None, SimpleNamespace(data=value))


class StrongPasswordValidatorTests(unittest.TestCase):
    def test_accepts_a_password_with_every_character_class(self):
        check("Duka#2026")

    def test_names_every_missing_rule(self):
        with self.assertRaises(ValidationError) as raised:
            check("lowercaseonly")
        message = str(raised.exception)
        self.assertIn("an uppercase letter", message)
        self.assertIn("a number", message)
        self.assertIn("a symbol", message)
        self.assertNotIn("a lowercase letter", message)

    def test_rejects_each_missing_class_on_its_own(self):
        for weak in ("duka#2026", "DUKA#2026", "Duka#duka", "Duka2026x"):
            with self.subTest(weak=weak), self.assertRaises(ValidationError):
                check(weak)

    def test_rejects_spaces(self):
        with self.assertRaises(ValidationError) as raised:
            check("Duka #2026")
        self.assertIn("spaces", str(raised.exception))


class RegistrationPasswordTests(unittest.TestCase):
    def setUp(self):
        self.app = create_app(PasswordPolicyConfig)
        self.app_context = self.app.app_context()
        self.app_context.push()
        db.create_all()
        self.client = self.app.test_client()

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.app_context.pop()

    def _register(self, password):
        return self.client.post(
            "/auth/register",
            data={
                "business_name": "Policy Shop",
                "owner_name": "Policy Owner",
                "email": "owner@example.com",
                "phone_number": "+254700000071",
                "password": password,
                "confirm_password": password,
            },
        )

    def test_weak_password_is_rejected_and_no_account_is_created(self):
        response = self._register("password123")

        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Add an uppercase letter, a symbol", response.data)
        self.assertEqual(Retailer.query.count(), 0)

    def test_short_password_is_rejected(self):
        response = self._register("Ab#1")

        self.assertIn(b"Use between 8 and 128 characters.", response.data)
        self.assertEqual(Retailer.query.count(), 0)

    def test_register_page_lists_the_password_rules(self):
        response = self.client.get("/auth/register")

        self.assertIn(b"data-password-rules", response.data)
        self.assertIn(b"A symbol such as", response.data)
        self.assertIn(b"password-rules.js", response.data)


    def test_password_pages_load_the_show_password_button(self):
        for url in ("/auth/register", "/auth/login", "/auth/forgot-password"):
            with self.subTest(url=url):
                self.assertIn(b"password-toggle.js", self.client.get(url).data)

if __name__ == "__main__":
    unittest.main()
