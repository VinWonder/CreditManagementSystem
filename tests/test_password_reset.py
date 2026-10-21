import re
import unittest
from datetime import datetime, timedelta
from unittest.mock import patch

from app import create_app
from app.extensions import db
from app.models import PasswordResetCode, Retailer


class PasswordResetConfig:
    APP_ENV = "testing"
    SECRET_KEY = "test-secret"
    SQLALCHEMY_DATABASE_URI = "sqlite://"
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    SQLALCHEMY_ENGINE_OPTIONS = {}
    AUTO_CREATE_DB = False
    WTF_CSRF_ENABLED = False
    DEFAULT_PHONE_REGION = "KE"
    PASSWORD_RESET_CODE_MINUTES = 10
    PASSWORD_RESET_MAX_ATTEMPTS = 3
    PASSWORD_RESET_RESEND_SECONDS = 60


OLD_PASSWORD = "Duka#2026"
NEW_PASSWORD = "Fresh#2027"


class PasswordResetTests(unittest.TestCase):
    def setUp(self):
        self.app = create_app(PasswordResetConfig)
        self.app_context = self.app.app_context()
        self.app_context.push()
        db.create_all()
        self.client = self.app.test_client()

        self.retailer = Retailer(
            business_name="Reset Shop",
            owner_name="Reset Owner",
            phone_number="+254700000021",
            email="owner@example.com",
            email_verified_at=datetime.utcnow(),
            password_hash="",
        )
        self.retailer.set_password(OLD_PASSWORD)
        db.session.add(self.retailer)
        db.session.commit()

        self.emails, self.texts = [], []
        email_patch = patch(
            "app.auth.routes.send_email",
            side_effect=lambda to, subject, body: self.emails.append((to, body)) or (True, "sent"),
        )
        sms_patch = patch(
            "app.auth.routes.send_verification_sms",
            side_effect=lambda to, body: self.texts.append((to, body)) or (True, "sent"),
        )
        self.send_email = email_patch.start()
        self.send_sms = sms_patch.start()
        self.addCleanup(email_patch.stop)
        self.addCleanup(sms_patch.stop)

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.app_context.pop()

    def _ask(self, account):
        return self.client.post("/auth/forgot-password", data={"account": account})

    def _code(self, messages=None):
        return re.search(r"code(?: is|:) (\d{6})", (messages or self.emails)[-1][1]).group(1)

    def _age_codes(self):
        PasswordResetCode.query.update({"created_at": datetime.utcnow() - timedelta(minutes=2)})
        db.session.commit()

    def test_code_is_emailed_when_asking_with_the_phone_number(self):
        response = self._ask("0700 000 021")

        self.assertTrue(response.headers["Location"].endswith("/auth/verify-reset-code"))
        self.assertEqual(self.emails[0][0], "owner@example.com")
        self.assertEqual(self.texts, [])
        page = self.client.get("/auth/verify-reset-code").get_data(as_text=True)
        self.assertIn("Check your email", page)
        self.assertIn("we have sent a 6-digit code to its email address", page)

    def test_code_is_emailed_when_asking_with_the_email(self):
        self._ask("Owner@Example.com")

        self.assertEqual(self.emails[0][0], "owner@example.com")

    def test_full_reset_changes_the_password(self):
        self._ask("owner@example.com")

        verify = self.client.post("/auth/verify-reset-code", data={"code": self._code()})
        self.assertTrue(verify.headers["Location"].endswith("/auth/reset-password"))
        self.client.post(
            "/auth/reset-password", data={"password": NEW_PASSWORD, "confirm_password": NEW_PASSWORD}
        )

        retailer = db.session.get(Retailer, self.retailer.retailer_id)
        self.assertTrue(retailer.check_password(NEW_PASSWORD))
        self.assertFalse(retailer.check_password(OLD_PASSWORD))

    def test_codes_are_stored_hashed(self):
        self._ask("owner@example.com")

        self.assertNotIn(self._code(), PasswordResetCode.query.one().code_hash)

    def test_unknown_accounts_get_the_same_answer_and_no_message(self):
        known = self._ask("owner@example.com")
        unknown = self._ask("nobody@example.com")

        self.assertEqual(known.headers["Location"], unknown.headers["Location"])
        self.assertEqual(len(self.emails), 1)
        self.assertIn(
            b"If an account matches",
            self.client.get("/auth/verify-reset-code").data,
        )

    def test_requests_within_a_minute_do_not_send_another_email(self):
        self._ask("owner@example.com")
        self._ask("owner@example.com")
        self.client.post("/auth/resend-reset-code")
        self.assertEqual(len(self.emails), 1)

        self._age_codes()
        self.client.post("/auth/resend-reset-code")

        self.assertEqual(len(self.emails), 2)
        self.assertEqual(PasswordResetCode.query.filter_by(used_at=None).count(), 1)

    def test_only_the_newest_code_works(self):
        self._ask("owner@example.com")
        first = self._code()
        self._age_codes()
        self.client.post("/auth/resend-reset-code")
        second = self._code()
        if first == second:
            self.skipTest("Both random codes matched")

        self.client.post("/auth/verify-reset-code", data={"code": first})
        self.assertIsNone(PasswordResetCode.query.one().verified_at)
        self.client.post("/auth/verify-reset-code", data={"code": second})
        self.assertIsNotNone(PasswordResetCode.query.one().verified_at)

    def test_wrong_codes_lock_the_code(self):
        self._ask("owner@example.com")
        code = self._code()
        wrong = "000000" if code != "000000" else "111111"

        for _ in range(PasswordResetConfig.PASSWORD_RESET_MAX_ATTEMPTS):
            self.client.post("/auth/verify-reset-code", data={"code": wrong})
        self.client.post("/auth/verify-reset-code", data={"code": code})

        self.assertIsNone(PasswordResetCode.query.one().verified_at)

    def test_expired_code_is_rejected(self):
        self._ask("owner@example.com")
        PasswordResetCode.query.update({"expires_at": datetime.utcnow() - timedelta(seconds=1)})
        db.session.commit()

        self.client.post("/auth/verify-reset-code", data={"code": self._code()})

        self.assertIsNone(PasswordResetCode.query.one().verified_at)

    def test_failed_email_leaves_no_usable_code(self):
        self.send_email.side_effect = lambda *args: (False, "The email could not be sent")

        self._ask("owner@example.com")

        self.assertEqual(PasswordResetCode.query.count(), 0)

    def test_accounts_without_email_fall_back_to_sms(self):
        self.retailer.email = None
        db.session.commit()

        self._ask("0700000021")

        self.assertEqual(self.emails, [])
        self.assertEqual(self.texts[0][0], "+254700000021")
        self.client.post("/auth/verify-reset-code", data={"code": self._code(self.texts)})
        self.assertIsNotNone(PasswordResetCode.query.one().verified_at)

    def test_forgot_password_page_asks_for_phone_or_email(self):
        page = self.client.get("/auth/forgot-password").get_data(as_text=True)

        self.assertIn("Phone number or email", page)
        self.assertIn("Email me a code", page)


if __name__ == "__main__":
    unittest.main()
