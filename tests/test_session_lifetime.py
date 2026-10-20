import unittest
from datetime import timedelta

from flask import g

import config as project_config
from app import create_app
from app.extensions import db
from app.models import Retailer


class SqliteTestConfig(project_config.Config):
    SQLALCHEMY_DATABASE_URI = "sqlite://"
    SQLALCHEMY_ENGINE_OPTIONS = {}
    AUTO_CREATE_DB = False
    SECRET_KEY = "test-secret"
    WTF_CSRF_ENABLED = False


class SessionLifetimeTests(unittest.TestCase):
    def setUp(self):
        self.app = create_app(SqliteTestConfig)
        self.app_context = self.app.app_context()
        self.app_context.push()
        db.create_all()
        retailer = Retailer(
            business_name="Test Shop",
            owner_name="Owner",
            phone_number="+254700000031",
            password_hash="x",
        )
        retailer.set_password("correct-password")
        db.session.add(retailer)
        db.session.commit()
        self.retailer_id = retailer.retailer_id

    def tearDown(self):
        g.pop("_login_user", None)
        db.session.remove()
        db.drop_all()
        self.app_context.pop()

    def _login(self, client):
        return client.post(
            "/auth/login",
            data={"phone_number": "+254700000031", "password": "correct-password"},
        )

    def test_configured_session_lifetime_is_bounded(self):
        lifetime = self.app.config["PERMANENT_SESSION_LIFETIME"]
        self.assertIsInstance(lifetime, timedelta)
        self.assertGreater(lifetime, timedelta(0))
        self.assertEqual(lifetime, self.app.config["REMEMBER_COOKIE_DURATION"])

    def test_login_creates_an_expiring_session(self):
        client = self.app.test_client()

        response = self._login(client)

        self.assertEqual(response.status_code, 302)
        self.assertEqual(response.headers["Location"], "/dashboard")
        with client.session_transaction() as sess:
            self.assertTrue(sess.get("_permanent"))
            self.assertEqual(sess["_user_id"], str(self.retailer_id))

    def test_session_without_the_browser_cookie_requires_login(self):
        logged_in = self.app.test_client()
        self._login(logged_in)
        self.assertEqual(logged_in.get("/dashboard").status_code, 200)

        # Flask-Login caches the user on g; clear it so the next client is judged
        # on its own cookie alone, as a restarted server would.
        g.pop("_login_user", None)

        # A fresh client has no session cookie, so the server must send it to log in.
        fresh = self.app.test_client()
        response = fresh.get("/dashboard")

        self.assertEqual(response.status_code, 302)
        self.assertIn("/auth/login", response.headers["Location"])


if __name__ == "__main__":
    unittest.main()
