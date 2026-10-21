import re
import unittest

from app import create_app
from app.extensions import db
from app.models import Retailer


class AuthSessionConfig:
    APP_ENV = "testing"
    SECRET_KEY = "test-secret"
    SQLALCHEMY_DATABASE_URI = "sqlite://"
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    SQLALCHEMY_ENGINE_OPTIONS = {}
    AUTO_CREATE_DB = False
    WTF_CSRF_ENABLED = True
    DEFAULT_PHONE_REGION = "KE"


class AuthSessionTests(unittest.TestCase):
    def setUp(self):
        self.app = create_app(AuthSessionConfig)
        self.app_context = self.app.app_context()
        self.app_context.push()
        db.create_all()
        self.client = self.app.test_client()

        retailer = Retailer(
            business_name="Session Shop",
            owner_name="Shop Owner",
            phone_number="+254700000091",
            password_hash="",
        )
        retailer.set_password("securepass123")
        db.session.add(retailer)
        db.session.commit()

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.app_context.pop()

    def _csrf_token(self, page):
        response = self.client.get(page)
        match = re.search(
            rb'name="csrf_token" type="hidden" value="([^"]+)"',
            response.data,
        )
        self.assertIsNotNone(match)
        return match.group(1).decode()

    def test_login_preserves_internal_destination_and_rejects_external_redirect(self):
        token = self._csrf_token("/auth/login?next=https://attacker.example")

        response = self.client.post(
            "/auth/login?next=https://attacker.example",
            data={
                "csrf_token": token,
                "phone_number": "+254700000091",
                "password": "securepass123",
            },
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.headers["Location"], "/dashboard")
        with self.client.session_transaction() as user_session:
            self.assertIn("_user_id", user_session)

    def test_logout_is_csrf_protected_post_and_ends_the_session(self):
        login_token = self._csrf_token("/auth/login")
        logged_in = self.client.post(
            "/auth/login",
            data={
                "csrf_token": login_token,
                "phone_number": "+254700000091",
                "password": "securepass123",
            },
        )
        self.assertEqual(logged_in.status_code, 302)

        get_logout = self.client.get("/auth/logout")
        self.assertEqual(get_logout.status_code, 405)
        logout_token = self._csrf_token("/dashboard")
        response = self.client.post(
            "/auth/logout", data={"csrf_token": logout_token}
        )

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.headers["Location"], "/")
        home = self.client.get("/")
        self.assertEqual(home.status_code, 200)
        self.assertIn(b"You have been logged out.", home.data)
        with self.client.session_transaction() as user_session:
            self.assertNotIn("_user_id", user_session)
        protected = self.client.get("/dashboard")
        self.assertEqual(protected.status_code, 302)
        self.assertTrue(protected.headers["Location"].startswith("/auth/login?"))


if __name__ == "__main__":
    unittest.main()
