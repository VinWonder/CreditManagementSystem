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
    # A sign-up whose email stays unverified this long is treated as abandoned:
    # its shop name, phone number and email can be registered by someone else.
    UNVERIFIED_SIGNUP_HOURS = int(os.environ.get("UNVERIFIED_SIGNUP_HOURS", 24))
    DEFAULT_PHONE_REGION = os.environ.get("DEFAULT_PHONE_REGION", "KE").upper()
    MAX_CONTENT_LENGTH = 3 * 1024 * 1024
    SHOP_LOGO_UPLOAD_FOLDER = os.path.join(basedir, "app", "static", "uploads", "logos")
    SMTP_HOST = os.environ.get("SMTP_HOST", "")
    SMTP_PORT = int(os.environ.get("SMTP_PORT", 587))
    SMTP_USERNAME = os.environ.get("SMTP_USERNAME", "")
    SMTP_PASSWORD = os.environ.get("SMTP_PASSWORD", "").replace(" ", "")
    SMTP_USE_TLS = os.environ.get("SMTP_USE_TLS", "true").lower() == "true"
    MAIL_FROM = os.environ.get("MAIL_FROM", "")
    # Brevo email API: use instead of SMTP where outgoing SMTP ports are blocked (e.g. Render free).
    # Pasted keys sometimes keep quotes or spaces around them.
    BREVO_API_KEY = os.environ.get("BREVO_API_KEY", "").strip().strip("'\"").strip()

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

    # Subscriptions: shops pay into the app's own Paybill/Till by M-Pesa prompt.
    # Plans and prices live in app/billing/plans.py. Leave the keys empty to
    # take payments by hand and record them with `flask billing activate`.
    BILLING_MPESA_ENVIRONMENT = os.environ.get("BILLING_MPESA_ENVIRONMENT", "production").strip().lower()
    BILLING_MPESA_ACCOUNT_TYPE = os.environ.get("BILLING_MPESA_ACCOUNT_TYPE", "paybill").strip().lower()
    BILLING_MPESA_SHORTCODE = os.environ.get("BILLING_MPESA_SHORTCODE", "").strip()
    BILLING_MPESA_TILL_NUMBER = os.environ.get("BILLING_MPESA_TILL_NUMBER", "").strip()
    BILLING_MPESA_CONSUMER_KEY = os.environ.get("BILLING_MPESA_CONSUMER_KEY", "").strip()
    BILLING_MPESA_CONSUMER_SECRET = os.environ.get("BILLING_MPESA_CONSUMER_SECRET", "").strip()
    BILLING_MPESA_PASSKEY = os.environ.get("BILLING_MPESA_PASSKEY", "").strip()
    # Secret path segment for Safaricom's Pay Bill confirmations to the app's own
    # Paybill; registered with `flask billing register-c2b`. Any long random text.
    BILLING_C2B_TOKEN = os.environ.get("BILLING_C2B_TOKEN", "").strip()
    # A bank Paybill shops pay into by hand, e.g. I&M Bank: 542542 and your
    # 14-digit account number. Shops then submit their M-Pesa code and you
    # confirm it with `flask billing confirm <code>`.
    BILLING_PAYBILL_NUMBER = os.environ.get("BILLING_PAYBILL_NUMBER", "").strip()
    BILLING_PAYBILL_ACCOUNT = os.environ.get("BILLING_PAYBILL_ACCOUNT", "").strip()
    BILLING_PAYBILL_NAME = os.environ.get("BILLING_PAYBILL_NAME", "").strip()
    # Shown on the plans page when there is no prompt or Paybill, e.g. "Pay to Till 123456".
    # USSD (*code#) mini-statements through Africa's Talking. USSD_CALLBACK_TOKEN
    # is a secret path segment: set the callback URL to
    # PUBLIC_BASE_URL/ussd/callback/<token>. Leave it empty to turn USSD off.
    USSD_CALLBACK_TOKEN = os.environ.get("USSD_CALLBACK_TOKEN", "").strip()
    # The code customers dial, shown in the app, e.g. *384*1234#.
    USSD_SERVICE_CODE = os.environ.get("USSD_SERVICE_CODE", "").strip()
    BILLING_PAY_INSTRUCTIONS = os.environ.get("BILLING_PAY_INSTRUCTIONS", "").strip()
    BILLING_SUPPORT_PHONE = os.environ.get("BILLING_SUPPORT_PHONE", "").strip()

    # Ageing buckets (days overdue) used in receivables report
    AGEING_BUCKETS = [(0, 7), (8, 14), (15, 30), (31, 60), (61, 10_000)]
