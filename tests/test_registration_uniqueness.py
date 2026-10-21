import unittest
from unittest.mock import patch

from sqlalchemy.exc import IntegrityError

from app import create_app
from app.auth import routes as auth_routes
from app.auth.identity import canonical_email
from app.extensions import db
from app.models import Retailer, Shopkeeper


class RegistrationConfig:
    APP_ENV = "testing"
    SECRET_KEY = "test-secret"
    SQLALCHEMY_DATABASE_URI = "sqlite://"
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    SQLALCHEMY_ENGINE_OPTIONS = {}
    AUTO_CREATE_DB = False
    WTF_CSRF_ENABLED = False
    DEFAULT_PHONE_REGION = "KE"
    EMAIL_VERIFICATION_CODE_MINUTES = 15
    EMAIL_VERIFICATION_MAX_ATTEMPTS = 5
    EMAIL_VERIFICATION_RESEND_SECONDS = 60


PASSWORD = "Duka#2026"


class RegistrationUniquenessTests(unittest.TestCase):
    def setUp(self):
        self.app = create_app(RegistrationConfig)
        self.app_context = self.app.app_context()
        self.app_context.push()
        db.create_all()
        self.client = self.app.test_client()
        email_patch = patch("app.auth.routes.send_email", return_value=(True, "sent"))
        email_patch.start()
        self.addCleanup(email_patch.stop)
        self._register(name="Mama Njeri Shop", phone="+254700000011", email="owner@gmail.com")

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.app_context.pop()

    def _register(self, name="Kioski Kubwa", phone="+254700000099", email="kioski@example.com",
                  password=PASSWORD, owner="New Owner"):
        return self.client.post(
            "/auth/register",
            data={
                "business_name": name,
                "owner_name": owner,
                "email": email,
                "phone_number": phone,
                "password": password,
                "confirm_password": password,
            },
        )

    def _assert_refused(self, response, message):
        self.assertEqual(response.status_code, 200)
        self.assertIn(message, response.get_data(as_text=True))
        self.assertEqual(Retailer.query.count(), 1)

    def test_a_different_shop_registers_normally(self):
        response = self._register()

        self.assertEqual(response.status_code, 302)
        self.assertEqual(Retailer.query.count(), 2)

    def test_shop_name_cannot_be_registered_twice(self):
        for name in ("Mama Njeri Shop", "mama njeri shop", "  MAMA   Njeri  Shop "):
            with self.subTest(name=name):
                self._assert_refused(self._register(name=name), "A shop with this name is already registered")

    def test_phone_number_cannot_be_registered_twice(self):
        for phone in ("+254700000011", "0700000011", "0700 000 011"):
            with self.subTest(phone=phone):
                self._assert_refused(self._register(phone=phone), "This phone number is already registered")

    def test_a_shopkeepers_phone_cannot_register_a_shop(self):
        db.session.add(Shopkeeper(retailer_id=1, full_name="Clerk", phone_number="+254700000022", password_hash="x"))
        db.session.commit()

        self._assert_refused(self._register(phone="0700000022"), "This phone number is already registered")

    def test_the_same_inbox_cannot_register_twice(self):
        for email in ("owner@gmail.com", "OWNER@Gmail.com", "owner+shop2@gmail.com",
                      "o.w.n.e.r@gmail.com", "owner@googlemail.com"):
            with self.subTest(email=email):
                self._assert_refused(self._register(email=email), "This email is already registered")

    def test_dots_still_matter_outside_gmail(self):
        self._register(email="first.last@example.com")

        response = self._register(name="Another Shop", phone="+254700000098", email="firstlast@example.com")

        self.assertEqual(response.status_code, 302)
        self.assertEqual(Retailer.query.count(), 3)

    def test_every_clash_is_shown_together_on_the_form(self):
        response = self._register(name="MAMA NJERI SHOP", phone="0700000011", email="owner+x@gmail.com")
        page = response.get_data(as_text=True)

        self.assertEqual(response.status_code, 200)
        self.assertIn("A shop with this name is already registered", page)
        self.assertIn("This phone number is already registered", page)
        self.assertIn("This email is already registered", page)
        self.assertEqual(Retailer.query.count(), 1)

    def test_names_are_tidied_and_the_inbox_is_recorded(self):
        self._register(name="  Kioski   Kubwa ", owner=" Jane   Doe ", email="Jane.Doe+shop@Gmail.com")

        retailer = Retailer.query.filter_by(phone_number="+254700000099").one()
        self.assertEqual(retailer.business_name, "Kioski Kubwa")
        self.assertEqual(retailer.owner_name, "Jane Doe")
        self.assertEqual(retailer.email, "jane.doe+shop@gmail.com")
        self.assertEqual(retailer.email_canonical, "janedoe@gmail.com")

    def test_password_cannot_contain_the_phone_number_or_email_name(self):
        self._assert_refused(
            self._register(password="Ab#700000099"), "Don&#39;t use your phone number in your password."
        )
        self._assert_refused(
            self._register(password="Kioski#2026"), "Don&#39;t use your email name in your password."
        )

    def test_database_refuses_duplicates_even_without_the_form(self):
        db.session.add(Retailer(business_name="MAMA NJERI SHOP", owner_name="X",
                                phone_number="+254700000055", password_hash="x"))
        with self.assertRaises(IntegrityError):
            db.session.commit()
        db.session.rollback()

        db.session.add(Retailer(business_name="Other", owner_name="X", phone_number="+254700000056",
                                email="owner+2@gmail.com", email_canonical=canonical_email("owner+2@gmail.com"),
                                password_hash="x"))
        with self.assertRaises(IntegrityError):
            db.session.commit()
        db.session.rollback()

    def test_simultaneous_registration_is_reported_on_the_right_field(self):
        real_check = auth_routes.registration_conflicts
        calls = []

        def miss_first_time(*args):
            calls.append(args)
            return {} if len(calls) == 1 else real_check(*args)

        with patch("app.auth.routes.registration_conflicts", side_effect=miss_first_time):
            response = self._register(name="mama njeri shop")

        self._assert_refused(response, "A shop with this name is already registered")


if __name__ == "__main__":
    unittest.main()
