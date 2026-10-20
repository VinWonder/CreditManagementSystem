"""Encryption helpers for retailer-owned messaging provider credentials."""

from cryptography.fernet import Fernet, InvalidToken
from flask import current_app

from app.models import MessagingSettings


class MessagingCredentialError(ValueError):
    """Raised when encrypted provider credentials cannot be safely used."""


def _fernet():
    key = current_app.config.get("MESSAGING_CREDENTIALS_ENCRYPTION_KEY")
    if not key:
        raise MessagingCredentialError(
            "Messaging credential encryption is not configured on this server."
        )
    try:
        return Fernet(key.encode("ascii"))
    except (AttributeError, UnicodeEncodeError, ValueError) as exc:
        raise MessagingCredentialError(
            "Messaging credential encryption is misconfigured on this server."
        ) from exc


def encrypt_secret(secret):
    if not secret:
        return None
    return _fernet().encrypt(secret.encode("utf-8")).decode("ascii")


def decrypt_secret(encrypted_secret):
    if not encrypted_secret:
        return ""
    try:
        return _fernet().decrypt(encrypted_secret.encode("ascii")).decode("utf-8")
    except (InvalidToken, UnicodeEncodeError) as exc:
        raise MessagingCredentialError(
            "Saved provider credentials cannot be decrypted. Verify the server encryption key."
        ) from exc


def load_retailer_credentials(retailer_id):
    settings = MessagingSettings.query.filter_by(retailer_id=retailer_id).first()
    if settings is None:
        return {
            "sms_provider": "africastalking",
            "at_username": "",
            "at_api_key": "",
            "twilio_account_sid": "",
            "twilio_auth_token": "",
            "twilio_from_number": "",
            "twilio_messaging_service_sid": "",
            "whatsapp_phone_id": "",
            "whatsapp_token": "",
        }

    return {
        "sms_provider": settings.sms_provider,
        "at_username": settings.at_username or "",
        "at_api_key": decrypt_secret(settings.at_api_key_encrypted),
        "twilio_account_sid": settings.twilio_account_sid or "",
        "twilio_auth_token": decrypt_secret(settings.twilio_auth_token_encrypted),
        "twilio_from_number": settings.twilio_from_number or "",
        "twilio_messaging_service_sid": settings.twilio_messaging_service_sid or "",
        "whatsapp_phone_id": settings.whatsapp_phone_id or "",
        "whatsapp_token": decrypt_secret(settings.whatsapp_token_encrypted),
    }
