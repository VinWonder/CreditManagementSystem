import unittest

from sqlalchemy.exc import ProgrammingError

from app import create_app


class DatabaseErrorConfig:
    APP_ENV = "testing"
    DEBUG = True
    SECRET_KEY = "test-secret"
    SQLALCHEMY_DATABASE_URI = "sqlite://"
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    SQLALCHEMY_ENGINE_OPTIONS = {}
    AUTO_CREATE_DB = False
    SCHEDULER_ENABLED = False
    WTF_CSRF_ENABLED = False


class DatabaseErrorHandlingTests(unittest.TestCase):
    def setUp(self):
        self.app = create_app(DatabaseErrorConfig)

        def fail_with_missing_column():
            raise ProgrammingError(
                "SELECT approval_status FROM payments",
                {},
                Exception("column payments.approval_status does not exist"),
            )

        self.app.add_url_rule(
            "/test-database-error",
            view_func=fail_with_missing_column,
        )
        self.client = self.app.test_client()

    def test_database_schema_errors_get_friendly_service_error_page(self):
        response = self.client.get("/test-database-error")

        self.assertEqual(response.status_code, 503)
        self.assertIn(b"Service temporarily unavailable", response.data)
        self.assertIn(b"flask --app run db upgrade", response.data)
        self.assertNotIn(b"sqlalchemy.exc.ProgrammingError", response.data)
        self.assertNotIn(b"payments.approval_status does not exist", response.data)
        self.assertNotIn(b"Traceback", response.data)


if __name__ == "__main__":
    unittest.main()
