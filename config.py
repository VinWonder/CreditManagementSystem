import os
from datetime import timedelta

from dotenv import load_dotenv

basedir = os.path.abspath(os.path.dirname(__file__))
load_dotenv(os.path.join(basedir, ".env"))


class Config:
    APP_ENV = os.environ.get("APP_ENV", "development").strip().lower()
    SECRET_KEY = os.environ.get("SECRET_KEY", "dev-secret-key-change-me")
    SESSION_COOKIE_HTTPONLY = True
    SESSION_COOKIE_SAMESITE = "Lax"
    # Sessions end on their own instead of lasting for the browser's lifetime:
    # a retailer idle for longer than this must sign in again.
    PERMANENT_SESSION_LIFETIME = timedelta(
        hours=int(os.environ.get("SESSION_IDLE_HOURS", 12))
    )
    REMEMBER_COOKIE_DURATION = PERMANENT_SESSION_LIFETIME
    # Sign everyone out when the server restarts. On by default in development;
    # with several Gunicorn workers enable it only together with --preload.
    SESSION_ENDS_ON_RESTART = os.environ.get(
        "SESSION_ENDS_ON_RESTART", "false" if APP_ENV == "production" else "true"
    ).lower() == "true"
    SESSION_COOKIE_SECURE = os.environ.get(
        "SESSION_COOKIE_SECURE", "true" if APP_ENV == "production" else "false"
    ).lower() == "true"
    DEBUG = APP_ENV != "production"
    AUTO_CREATE_DB = os.environ.get(
        "AUTO_CREATE_DB", "false" if APP_ENV == "production" else "true"
    ).lower() == "true"
    PASSWORD_RESET_CODE_MINUTES = int(os.environ.get("PASSWORD_RESET_CODE_MINUTES", 10))
    PASSWORD_RESET_MAX_ATTEMPTS = int(os.environ.get("PASSWORD_RESET_MAX_ATTEMPTS", 5))
    PASSWORD_RESET_RESEND_SECONDS = int(os.environ.get("PASSWORD_RESET_RESEND_SECONDS", 60))
    PHONE_VERIFICATION_CODE_MINUTES = int(os.environ.get("PHONE_VERIFICATION_CODE_MINUTES", 10))
    PHONE_VERIFICATION_MAX_ATTEMPTS = int(os.environ.get("PHONE_VERIFICATION_MAX_ATTEMPTS", 5))
    LOGIN_OTP_CODE_MINUTES = int(os.environ.get("LOGIN_OTP_CODE_MINUTES", 5))
    LOGIN_OTP_MAX_ATTEMPTS = int(os.environ.get("LOGIN_OTP_MAX_ATTEMPTS", 5))
    LOGIN_OTP_RESEND_SECONDS = int(os.environ.get("LOGIN_OTP_RESEND_SECONDS", 60))
    EMAIL_VERIFICATION_CODE_MINUTES = int(os.environ.get("EMAIL_VERIFICATION_CODE_MINUTES", 15))
    EMAIL_VERIFICATION_MAX_ATTEMPTS = int(os.environ.get("EMAIL_VERIFICATION_MAX_ATTEMPTS", 5))
    EMAIL_VERIFICATION_RESEND_SECONDS = int(os.environ.get("EMAIL_VERIFICATION_RESEND_SECONDS", 60))
    DEFAULT_PHONE_REGION = os.environ.get("DEFAULT_PHONE_REGION", "KE").upper()
    MAX_CONTENT_LENGTH = 3 * 1024 * 1024
    SHOP_LOGO_UPLOAD_FOLDER = os.path.join(basedir, "app", "static", "uploads", "logos")
    SMTP_HOST = os.environ.get("SMTP_HOST", "")
    SMTP_PORT = int(os.environ.get("SMTP_PORT", 587))
    SMTP_USERNAME = os.environ.get("SMTP_USERNAME", "")
    SMTP_PASSWORD = os.environ.get("SMTP_PASSWORD", "").replace(" ", "")
    SMTP_USE_TLS = os.environ.get("SMTP_USE_TLS", "true").lower() == "true"
    MAIL_FROM = os.environ.get("MAIL_FROM", "")

    SQLALCHEMY_DATABASE_URI = os.environ.get(
        "DATABASE_URL", "sqlite:///" + os.path.join(basedir, "credit_system.db")
    )
    # Render/Heroku style URLs use postgres:// which SQLAlchemy 1.4+/2.x needs as postgresql://
    if SQLALCHEMY_DATABASE_URI.startswith("postgres://"):
        SQLALCHEMY_DATABASE_URI = SQLALCHEMY_DATABASE_URI.replace(
            "postgres://", "postgresql+psycopg://", 1
        )
    elif SQLALCHEMY_DATABASE_URI.startswith("postgresql://"):
        SQLALCHEMY_DATABASE_URI = SQLALCHEMY_DATABASE_URI.replace(
            "postgresql://", "postgresql+psycopg://", 1
        )

    SQLALCHEMY_TRACK_MODIFICATIONS = False
    SQLALCHEMY_ENGINE_OPTIONS = {"pool_pre_ping": True}

    # SMS / WhatsApp
    SMS_PROVIDER = os.environ.get("SMS_PROVIDER", "africastalking").strip().lower()
    AT_USERNAME = os.environ.get("AT_USERNAME", "sandbox")
    AT_API_KEY = os.environ.get("AT_API_KEY", "")
    TWILIO_ACCOUNT_SID = os.environ.get("TWILIO_ACCOUNT_SID", "")
    TWILIO_AUTH_TOKEN = os.environ.get("TWILIO_AUTH_TOKEN", "")
    TWILIO_FROM_NUMBER = os.environ.get("TWILIO_FROM_NUMBER", "")
    TWILIO_MESSAGING_SERVICE_SID = os.environ.get("TWILIO_MESSAGING_SERVICE_SID", "")
    WHATSAPP_TOKEN = os.environ.get("WHATSAPP_TOKEN", "")
    WHATSAPP_PHONE_ID = os.environ.get("WHATSAPP_PHONE_ID", "")
    MESSAGING_CREDENTIALS_ENCRYPTION_KEY = os.environ.get(
        "MESSAGING_CREDENTIALS_ENCRYPTION_KEY", ""
    )

    # M-Pesa STK Push: each shop's Paybill/Till is saved with `flask mpesa set`.
    # Public HTTPS address Safaricom calls back with each payment result.
    PUBLIC_BASE_URL = os.environ.get("PUBLIC_BASE_URL", "").strip().rstrip("/")
    MPESA_REQUEST_TIMEOUT_SECONDS = int(os.environ.get("MPESA_REQUEST_TIMEOUT_SECONDS", 30))

    # Ageing buckets (days overdue) used in receivables report
    AGEING_BUCKETS = [(0, 7), (8, 14), (15, 30), (31, 60), (61, 10_000)]
