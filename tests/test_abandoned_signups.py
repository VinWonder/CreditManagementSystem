import unittest
from datetime import datetime, timedelta
from unittest.mock import patch

from app import create_app
from app.auth.identity import canonical_email
from app.extensions import db
from app.models import Customer, EmailVerificationCode, Retailer


class AbandonedSignupConfig:
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
    UNVERIFIED_SIGNUP_HOURS = 24


PASSWORD = "Duka#2026"


class AbandonedSignupTests(unittest.TestCase):
    def setUp(self):
        self.app = create_app(AbandonedSignupConfig)
        self.app_context = self.app.app_context()
        self.app_context.push()
        db.create_all()
        self.client = self.app.test_client()
        email_patch = patch("app.auth.routes.send_email", return_value=(True, "sent"))
        email_patch.start()
        self.addCleanup(email_patch.stop)

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.app_context.pop()

    def _existing(self, hours_ago, verified=False, name="Mama Njeri Shop",
                  phone="+254700000011", email="owner@gmail.com"):
        retailer = Retailer(
            business_name=name,
            owner_name="First Owner",
            phone_number=phone,
            email=email,
            email_canonical=canonical_email(email),
            email_verified_at=datetime.utcnow() if verified else None,
            created_at=datetime.utcnow() - timedelta(hours=hours_ago),
            password_hash="x",
        )
        db.session.add(retailer)
        db.session.commit()
        return retailer.retailer_id

    def _register(self, name="Mama Njeri Shop", phone="+254700000099", email="new@example.com"):
        return self.client.post(
            "/auth/register",
            data={
                "business_name": name,
                "owner_name": "New Owner",
                "email": email,
                "phone_number": phone,
                "password": PASSWORD,
                "confirm_password": PASSWORD,
            },
        )

    def test_abandoned_signup_releases_its_shop_name(self):
        self._existing(hours_ago=25)

        response = self._register(name="MAMA NJERI SHOP")

        self.assertEqual(response.status_code, 302)
        # SQLite may reuse the freed id, so check by the old account's phone.
        self.assertIsNone(Retailer.query.filter_by(phone_number="+254700000011").first())
        self.assertEqual(Retailer.query.one().phone_number, "+254700000099")

    def test_abandoned_signup_releases_its_phone_and_email(self):
        self._existing(hours_ago=48)

        response = self._register(name="Different Shop", phone="0700000011", email="owner+2@gmail.com")

        self.assertEqual(response.status_code, 302)
        self.assertEqual(Retailer.query.one().business_name, "Different Shop")

    def test_recent_unverified_signup_still_blocks(self):
        self._existing(hours_ago=2)

        response = self._register()

        self.assertIn(b"A shop with this name is already registered", response.data)
        self.assertEqual(Retailer.query.count(), 1)

    def test_verified_accounts_are_never_released(self):
        self._existing(hours_ago=24 * 365, verified=True)

        response = self._register()

        self.assertIn(b"A shop with this name is already registered", response.data)
        self.assertEqual(Retailer.query.count(), 1)

    def test_old_accounts_without_email_are_never_released(self):
        retailer_id = self._existing(hours_ago=24 * 365)
        retailer = db.session.get(Retailer, retailer_id)
        retailer.email = None
        retailer.email_canonical = None
        db.session.commit()

        response = self._register()

        self.assertIn(b"A shop with this name is already registered", response.data)

    def test_accounts_with_shop_records_are_never_released(self):
        old_id = self._existing(hours_ago=72)
        db.session.add(Customer(retailer_id=old_id, full_name="A Customer", phone_number="+254711000000"))
        db.session.commit()

        response = self._register()

        self.assertIn(b"A shop with this name is already registered", response.data)
        self.assertIsNotNone(db.session.get(Retailer, old_id))

    def test_released_signups_take_their_codes_with_them(self):
        old_id = self._existing(hours_ago=30)
        old_code = EmailVerificationCode(retailer_id=old_id, code_hash="old-code",
                                         expires_at=datetime.utcnow())
        db.session.add(old_code)
        db.session.commit()

        self._register()

        self.assertEqual(EmailVerificationCode.query.filter_by(code_hash="old-code").count(), 0)

    def test_purge_command_lists_then_deletes_abandoned_signups(self):
        self._existing(hours_ago=30)
        self._existing(hours_ago=1, name="Fresh Shop", phone="+254700000012", email="fresh@example.com")
        self._existing(hours_ago=90, verified=True, name="Real Shop", phone="+254700000013", email="real@example.com")
        runner = self.app.test_cli_runner()

        dry = runner.invoke(args=["accounts", "purge-unverified", "--dry-run"])
        self.assertIn("Would delete 1 sign-up", dry.output)
        self.assertEqual(Retailer.query.count(), 3)

        real = runner.invoke(args=["accounts", "purge-unverified"])
        self.assertIn("Deleted 1 sign-up", real.output)
        self.assertEqual(
            sorted(r.business_name for r in Retailer.query.all()), ["Fresh Shop", "Real Shop"]
        )


if __name__ == "__main__":
    unittest.main()
