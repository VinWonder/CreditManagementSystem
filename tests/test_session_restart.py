import unittest

from app import create_app
from app.extensions import db
from app.models import Retailer


class RestartConfig:
    APP_ENV = "testing"
    SECRET_KEY = "test-secret"
    SQLALCHEMY_DATABASE_URI = "sqlite://"
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    SQLALCHEMY_ENGINE_OPTIONS = {}
    AUTO_CREATE_DB = False
    WTF_CSRF_ENABLED = False
    DEFAULT_PHONE_REGION = "KE"
    SESSION_ENDS_ON_RESTART = True


class SessionRestartTests(unittest.TestCase):
    def setUp(self):
        self.app = create_app(RestartConfig)
        self.app_context = self.app.app_context()
        self.app_context.push()
        db.create_all()
        retailer = Retailer(
            business_name="Restart Shop",
            owner_name="Restart Owner",
            phone_number="+254700000061",
            password_hash="",
        )
        retailer.set_password("Duka#2026")
        db.session.add(retailer)
        db.session.commit()
        self.client = self.app.test_client()
        self.client.post(
            "/auth/login",
            data={"phone_number": "+254700000061", "password": "Duka#2026"},
        )

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.app_context.pop()

    def _restart_server(self):
        self.app.config["SERVER_BOOT_ID"] = "a-new-server-start"

    def test_login_stamps_the_current_server_start(self):
        with self.client.session_transaction() as session:
            self.assertEqual(session["_server_boot_id"], self.app.config["SERVER_BOOT_ID"])
        self.assertEqual(self.client.get("/dashboard").status_code, 200)

    def test_session_from_before_a_restart_is_signed_out(self):
        self._restart_server()

        response = self.client.get("/dashboard")

        self.assertEqual(response.status_code, 302)
        self.assertIn("/auth/login", response.headers["Location"])
        with self.client.session_transaction() as session:
            self.assertNotIn("_user_id", session)

    def test_signing_in_again_after_a_restart_works(self):
        self._restart_server()
        self.client.get("/dashboard")

        self.client.post(
            "/auth/login",
            data={"phone_number": "+254700000061", "password": "Duka#2026"},
        )

        self.assertEqual(self.client.get("/dashboard").status_code, 200)

    def test_setting_can_keep_sessions_across_restarts(self):
        self.app.config["SESSION_ENDS_ON_RESTART"] = False
        self._restart_server()

        self.assertEqual(self.client.get("/dashboard").status_code, 200)


if __name__ == "__main__":
    unittest.main()
