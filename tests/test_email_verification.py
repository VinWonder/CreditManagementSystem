import re
import smtplib
import unittest
from datetime import datetime, timedelta
from unittest.mock import patch

from app import create_app
from app.auth.email import send_email
from app.extensions import db
from app.models import EmailVerificationCode, Retailer


class EmailVerificationConfig:
    APP_ENV = "testing"
    SECRET_KEY = "test-secret"
    SQLALCHEMY_DATABASE_URI = "sqlite://"
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    SQLALCHEMY_ENGINE_OPTIONS = {}
    AUTO_CREATE_DB = False
    WTF_CSRF_ENABLED = False
    DEFAULT_PHONE_REGION = "KE"
    EMAIL_VERIFICATION_CODE_MINUTES = 15
    EMAIL_VERIFICATION_MAX_ATTEMPTS = 3
    EMAIL_VERIFICATION_RESEND_SECONDS = 60
    SMTP_HOST = ""


PASSWORD = "Duka#2026"


class EmailVerificationTests(unittest.TestCase):
    def setUp(self):
        self.app = create_app(EmailVerificationConfig)
        self.app_context = self.app.app_context()
        self.app_context.push()
        db.create_all()
        self.client = self.app.test_client()
        self.sent = []

        def fake_send(to_address, subject, body):
            self.sent.append({"to": to_address, "subject": subject, "body": body})
            return True, "sent"

        patcher = patch("app.auth.routes.send_email", side_effect=fake_send)
        self.send_email = patcher.start()
        self.addCleanup(patcher.stop)

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.app_context.pop()

    def _register(self, email="Owner@Example.com"):
        return self.client.post(
            "/auth/register",
            data={
                "business_name": "Verify Shop",
                "owner_name": "Verify Owner",
                "email": email,
                "phone_number": "+254700000081",
                "password": PASSWORD,
                "confirm_password": PASSWORD,
            },
        )

    def _login(self):
        return self.client.post(
            "/auth/login",
            data={"phone_number": "+254700000081", "password": PASSWORD},
        )

    def _last_code(self):
        return re.search(r"code is (\d{6})", self.sent[-1]["body"]).group(1)

    def _is_signed_in(self):
        return self.client.get("/dashboard").status_code == 200

    def test_registration_emails_a_code_and_does_not_sign_in(self):
        response = self._register()

        self.assertEqual(response.status_code, 302)
        self.assertTrue(response.headers["Location"].endswith("/auth/verify-email"))
        self.assertEqual(len(self.sent), 1)
        self.assertEqual(self.sent[0]["to"], "owner@example.com")
        retailer = Retailer.query.one()
        self.assertIsNone(retailer.email_verified_at)
        self.assertFalse(self._is_signed_in())

        page = self.client.get("/auth/verify-email")
        self.assertIn(b"Verify your email", page.data)
        self.assertIn(b"owner@example.com", page.data)

    def test_codes_are_stored_hashed(self):
        self._register()

        stored = EmailVerificationCode.query.one()
        self.assertNotEqual(stored.code_hash, self._last_code())
        self.assertNotIn(self._last_code(), stored.code_hash)

    def test_correct_code_verifies_the_email_and_signs_in(self):
        self._register()

        response = self.client.post("/auth/verify-email", data={"code": self._last_code()})

        self.assertEqual(response.status_code, 302)
        self.assertTrue(response.headers["Location"].endswith("/dashboard"))
        self.assertIsNotNone(Retailer.query.one().email_verified_at)
        self.assertIsNotNone(EmailVerificationCode.query.one().used_at)
        self.assertTrue(self._is_signed_in())

    def test_wrong_codes_count_attempts_and_lock_the_code(self):
        self._register()
        code = self._last_code()
        wrong = "000000" if code != "000000" else "111111"

        for _ in range(EmailVerificationConfig.EMAIL_VERIFICATION_MAX_ATTEMPTS):
            response = self.client.post("/auth/verify-email", data={"code": wrong})
            self.assertEqual(response.status_code, 200)

        self.client.post("/auth/verify-email", data={"code": code})
        self.assertIsNone(Retailer.query.one().email_verified_at)
        self.assertFalse(self._is_signed_in())

    def test_expired_code_is_rejected(self):
        self._register()
        stored = EmailVerificationCode.query.one()
        stored.expires_at = datetime.utcnow() - timedelta(seconds=1)
        db.session.commit()

        self.client.post("/auth/verify-email", data={"code": self._last_code()})

        self.assertIsNone(Retailer.query.one().email_verified_at)
        self.assertFalse(self._is_signed_in())

    def test_unverified_retailer_login_is_sent_to_the_code_page(self):
        self._register()
        EmailVerificationCode.query.update({"sent_at": datetime.utcnow() - timedelta(minutes=5)})
        db.session.commit()
        fresh_client = self.app.test_client()
        self.client = fresh_client

        response = self._login()

        self.assertTrue(response.headers["Location"].endswith("/auth/verify-email"))
        self.assertEqual(len(self.sent), 2)
        self.assertFalse(self._is_signed_in())
        self.client.post("/auth/verify-email", data={"code": self._last_code()})
        self.assertTrue(self._is_signed_in())

    def test_verified_retailer_logs_in_directly(self):
        self._register()
        self.client.post("/auth/verify-email", data={"code": self._last_code()})
        self.client.post("/auth/logout")
        self.client = self.app.test_client()

        response = self._login()

        self.assertTrue(response.headers["Location"].endswith("/dashboard"))
        self.assertEqual(len(self.sent), 1)

    def test_retailers_from_before_verification_are_not_blocked(self):
        retailer = Retailer(
            business_name="Old Shop",
            owner_name="Old Owner",
            phone_number="+254700000081",
            password_hash="",
        )
        retailer.set_password(PASSWORD)
        db.session.add(retailer)
        db.session.commit()

        response = self._login()

        self.assertTrue(response.headers["Location"].endswith("/dashboard"))
        self.assertEqual(self.sent, [])

    def test_resend_is_rate_limited(self):
        self._register()

        self.client.post("/auth/resend-email-verification")
        self.assertEqual(len(self.sent), 1)

        EmailVerificationCode.query.update({"sent_at": datetime.utcnow() - timedelta(minutes=2)})
        db.session.commit()
        self.client.post("/auth/resend-email-verification")
        self.assertEqual(len(self.sent), 2)
        self.assertEqual(
            EmailVerificationCode.query.filter_by(used_at=None).count(), 1
        )

    def test_failed_email_leaves_no_usable_code(self):
        self.send_email.side_effect = lambda *args: (False, "The email could not be sent")

        self._register()
        page = self.client.get("/auth/verify-email")

        self.assertEqual(EmailVerificationCode.query.count(), 0)
        self.assertIn(b"could not send the verification email", page.data)

    def test_retailer_is_told_when_email_is_not_set_up(self):
        self.send_email.side_effect = lambda *args: (True, "simulated")

        self._register()
        page = self.client.get("/auth/verify-email")

        self.assertIn(b"Email sending is not set up on this server yet", page.data)
        self.assertNotIn(b"We sent a verification code", page.data)

    def test_real_send_confirms_the_address(self):
        self._register()
        page = self.client.get("/auth/verify-email")

        self.assertIn(b"We sent a verification code to owner@example.com", page.data)

    def test_verify_page_without_a_pending_signup_goes_to_login(self):
        response = self.client.get("/auth/verify-email")

        self.assertTrue(response.headers["Location"].endswith("/auth/login"))


class SendEmailTests(unittest.TestCase):
    def _app(self, **overrides):
        config = type("Config", (EmailVerificationConfig,), overrides)
        return create_app(config)

    def test_development_without_smtp_is_simulated(self):
        with self._app().app_context():
            self.assertEqual(send_email("a@example.com", "s", "b"), (True, "simulated"))

    def test_production_without_smtp_refuses_to_send(self):
        app = self._app()
        with app.app_context():
            app.config["APP_ENV"] = "production"
            sent, _ = send_email("a@example.com", "s", "b")
            self.assertFalse(sent)

    def _smtp_app(self):
        return self._app(
            SMTP_HOST="smtp.example.com",
            SMTP_PORT=587,
            SMTP_USERNAME="shop@example.com",
            SMTP_PASSWORD="app-password",
            SMTP_USE_TLS=True,
            MAIL_FROM="shop@example.com",
        )

    def test_refused_login_explains_app_passwords(self):
        with self._smtp_app().app_context(), patch("app.auth.email.smtplib.SMTP") as smtp:
            smtp.return_value.login.side_effect = smtplib.SMTPAuthenticationError(535, b"bad")
            sent, reason = send_email("owner@example.com", "Code", "Body")

        self.assertFalse(sent)
        self.assertIn("app password", reason)
        self.assertNotIn("app-password", reason)

    def test_connection_failure_names_the_server(self):
        with self._smtp_app().app_context(), patch(
            "app.auth.email.smtplib.SMTP", side_effect=OSError("timed out")
        ):
            sent, reason = send_email("owner@example.com", "Code", "Body")

        self.assertFalse(sent)
        self.assertIn("smtp.example.com:587", reason)

    def test_cli_reports_missing_smtp_settings(self):
        result = self._app().test_cli_runner().invoke(args=["send-test-email", "a@example.com"])

        self.assertEqual(result.exit_code, 1)
        self.assertIn("SMTP_HOST is empty", result.output)

    def test_cli_sends_a_test_email(self):
        app = self._smtp_app()
        with patch("app.auth.email.smtplib.SMTP") as smtp:
            result = app.test_cli_runner().invoke(args=["send-test-email", "owner@example.com"])

        self.assertEqual(result.exit_code, 0, result.output)
        self.assertIn("Sent.", result.output)
        smtp.return_value.send_message.assert_called_once()

    def test_smtp_settings_are_used_to_send(self):
        app = self._app(
            SMTP_HOST="smtp.example.com",
            SMTP_PORT=587,
            SMTP_USERNAME="shop@example.com",
            SMTP_PASSWORD="app-password",
            SMTP_USE_TLS=True,
            MAIL_FROM="Duka Credit <shop@example.com>",
        )
        with app.app_context(), patch("app.auth.email.smtplib.SMTP") as smtp:
            sent, _ = send_email("owner@example.com", "Code", "Body")

        self.assertTrue(sent)
        server = smtp.return_value
        server.starttls.assert_called_once()
        server.login.assert_called_once_with("shop@example.com", "app-password")
        message = server.send_message.call_args.args[0]
        self.assertEqual(message["To"], "owner@example.com")
        self.assertEqual(message["From"], "Duka Credit <shop@example.com>")


if __name__ == "__main__":
    unittest.main()
