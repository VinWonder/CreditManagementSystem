import io
import os
import re
import shutil
import tempfile
import unittest
from datetime import datetime
from unittest.mock import patch

from flask import g
from flask_login import login_user

from app import create_app
from app.extensions import db
from app.models import Retailer, Shopkeeper

PASSWORD = "Duka#2026"
# Smallest valid PNG (1x1 pixel).
PNG = bytes.fromhex(
    "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c489"
    "0000000d49444154789c6360000002000154a24f5d0000000049454e44ae426082"
)


class AccountConfig:
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


class AccountProfileTests(unittest.TestCase):
    def setUp(self):
        self.upload_dir = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.upload_dir, ignore_errors=True)
        config = type("Config", (AccountConfig,), {"SHOP_LOGO_UPLOAD_FOLDER": self.upload_dir})
        self.app = create_app(config)
        self.app_context = self.app.app_context()
        self.app_context.push()
        db.create_all()
        self.client = self.app.test_client()
        self.retailer = Retailer(business_name="Mama Njeri Shop", owner_name="Njeri", phone_number="+254700000101",
                                 email="njeri@example.com", email_canonical="njeri@example.com",
                                 email_verified_at=datetime.utcnow(), password_hash="")
        self.retailer.set_password(PASSWORD)
        self.other = Retailer(business_name="Other Shop", owner_name="Other", phone_number="+254700000102",
                              email="other@example.com", email_canonical="other@example.com",
                              email_verified_at=datetime.utcnow(), password_hash="x")
        db.session.add_all([self.retailer, self.other])
        db.session.flush()
        self.shopkeeper = Shopkeeper(retailer_id=self.retailer.retailer_id, full_name="Clerk Kamau",
                                     phone_number="+254700000103", password_hash="")
        self.shopkeeper.set_password(PASSWORD)
        db.session.add(self.shopkeeper)
        db.session.commit()
        self.user = self.retailer
        self.sent = []

        @self.app.before_request
        def authenticate():
            g.pop("_login_user", None)
            login_user(self.user)

        email_patch = patch("app.auth.routes.send_email",
                            side_effect=lambda to, subject, body: self.sent.append((to, body)) or (True, "sent"))
        self.send_email = email_patch.start()
        self.addCleanup(email_patch.stop)

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.app_context.pop()

    def _profile(self, **changes):
        data = {"business_name": "Mama Njeri Shop", "owner_name": "Njeri", "phone_number": "+254700000101",
                "current_password": ""}
        data.update(changes)
        return self.client.post("/account/profile", data=data, content_type="multipart/form-data")

    def _reload(self):
        return db.session.get(Retailer, self.retailer.retailer_id)

    # Menu

    def test_account_menu_offers_profile_password_and_email(self):
        page = self.client.get("/dashboard").get_data(as_text=True)

        self.assertIn('data-bs-toggle="dropdown"', page)
        for link in ("/account/profile", "/account/password", "/account/email"):
            self.assertIn(link, page)

    def test_shopkeepers_get_profile_and_password_but_not_email(self):
        self.user = self.shopkeeper

        page = self.client.get("/dashboard").get_data(as_text=True)
        profile = self.client.get("/account/profile").get_data(as_text=True)

        self.assertIn("/account/password", page)
        self.assertNotIn("/account/email", page)
        self.assertIn("Clerk Kamau", profile)
        self.assertIn("Ask your shop admin", profile)
        self.assertEqual(self.client.get("/account/email").status_code, 403)

    # Profile details

    def test_shop_and_owner_names_can_be_updated(self):
        response = self._profile(business_name="  Mama Njeri   Wholesale ", owner_name="Njeri Wambui")

        self.assertEqual(response.status_code, 302)
        self.assertEqual((self._reload().business_name, self._reload().owner_name),
                         ("Mama Njeri Wholesale", "Njeri Wambui"))

    def test_another_shops_name_cannot_be_taken(self):
        response = self._profile(business_name="other shop")

        self.assertIn(b"Another shop already uses this name", response.data)
        self.assertEqual(self._reload().business_name, "Mama Njeri Shop")

    def test_phone_change_needs_the_current_password(self):
        refused = self._profile(phone_number="0700000109")
        self.assertIn(b"Enter your current password to change the phone number", refused.data)

        self._profile(phone_number="0700000109", current_password=PASSWORD)

        self.assertEqual(self._reload().phone_number, "+254700000109")

    def test_phone_used_by_another_account_is_refused(self):
        for phone in ("0700000102", "0700000103"):
            with self.subTest(phone=phone):
                response = self._profile(phone_number=phone, current_password=PASSWORD)
                self.assertIn(b"already used by another account", response.data)
        self.assertEqual(self._reload().phone_number, "+254700000101")

    def test_logo_can_be_uploaded_and_removed(self):
        self._profile(logo=(io.BytesIO(PNG), "logo.png"))
        logo = self._reload().logo_filename
        self.assertTrue(logo and os.path.exists(os.path.join(self.upload_dir, logo)))

        self._profile(remove_logo="y")

        self.assertIsNone(self._reload().logo_filename)
        self.assertFalse(os.path.exists(os.path.join(self.upload_dir, logo)))

    # Password

    def test_password_change_checks_the_current_password_and_rules(self):
        wrong = self.client.post("/account/password", data={
            "current_password": "Nope#1234", "password": "Fresh#2027", "confirm_password": "Fresh#2027"})
        weak = self.client.post("/account/password", data={
            "current_password": PASSWORD, "password": "weakpass", "confirm_password": "weakpass"})

        self.assertIn(b"That is not your current password.", wrong.data)
        self.assertIn(b"Add an uppercase letter", weak.data)
        self.assertTrue(self._reload().check_password(PASSWORD))

        self.client.post("/account/password", data={
            "current_password": PASSWORD, "password": "Fresh#2027", "confirm_password": "Fresh#2027"})
        self.assertTrue(self._reload().check_password("Fresh#2027"))

    def test_shopkeepers_can_change_their_own_password(self):
        self.user = self.shopkeeper

        self.client.post("/account/password", data={
            "current_password": PASSWORD, "password": "Fresh#2027", "confirm_password": "Fresh#2027"})

        self.assertTrue(db.session.get(Shopkeeper, self.shopkeeper.shopkeeper_id).check_password("Fresh#2027"))

    # Email

    def _code(self):
        return re.search(r"code is (\d{6})", self.sent[-1][1]).group(1)

    def test_email_changes_only_after_the_code_from_the_new_inbox(self):
        response = self.client.post("/account/email", data={"email": "New@Example.com", "current_password": PASSWORD})

        self.assertTrue(response.headers["Location"].endswith("/account/email/confirm"))
        self.assertEqual(self.sent[-1][0], "new@example.com")
        self.assertEqual(self._reload().email, "njeri@example.com")
        self.assertEqual(self._reload().pending_email, "new@example.com")

        wrong = "000000" if self._code() != "000000" else "111111"
        self.client.post("/account/email/confirm", data={"code": wrong})
        self.assertEqual(self._reload().email, "njeri@example.com")

        self.client.post("/account/email/confirm", data={"code": self._code()})
        retailer = self._reload()
        self.assertEqual((retailer.email, retailer.email_canonical, retailer.pending_email),
                         ("new@example.com", "new@example.com", None))

    def test_email_change_is_refused_when_it_should_be(self):
        cases = [
            ({"email": "new@example.com", "current_password": "Nope#1234"}, b"That is not your current password."),
            ({"email": "NJERI@example.com", "current_password": PASSWORD}, b"This is already your email."),
            ({"email": "other+2@example.com", "current_password": PASSWORD}, b"already registered to another shop"),
        ]
        for data, message in cases:
            with self.subTest(email=data["email"]):
                self.assertIn(message, self.client.post("/account/email", data=data).data)
        self.assertEqual(self.sent, [])

    def test_failed_code_email_leaves_the_email_unchanged(self):
        self.send_email.side_effect = lambda *args: (False, "Could not connect")

        response = self.client.post("/account/email", data={"email": "new@example.com", "current_password": PASSWORD})

        self.assertIn(b"couldn&#39;t send a code to new@example.com", response.data)
        self.assertIsNone(self._reload().pending_email)


if __name__ == "__main__":
    unittest.main()
