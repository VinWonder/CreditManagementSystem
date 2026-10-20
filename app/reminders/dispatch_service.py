"""
Message dispatch service.

Sends reminders via SMS (Africa's Talking - recommended for Kenya: cheap,
reliable local delivery, simple REST API) or WhatsApp (Meta WhatsApp
Business Cloud API - free-tier friendly, rich formatting, read receipts).

Messages are never reported as sent unless the selected provider accepts
them. A provider acceptance is not confirmation of handset delivery.
"""
from datetime import datetime
import re

import requests
from flask import current_app

from app.extensions import db
from app.models import MessageDispatchLog, MessagingSettings
from app.reminders.credentials import (
    MessagingCredentialError,
    load_retailer_credentials,
)


def _credentials_present_sms(credentials=None):
    provider = (
        credentials["sms_provider"]
        if credentials is not None
        else current_app.config.get("SMS_PROVIDER", "africastalking").lower()
    )
    if provider == "twilio":
        account_sid = (
            credentials["twilio_account_sid"]
            if credentials is not None
            else current_app.config.get("TWILIO_ACCOUNT_SID")
        )
        auth_token = (
            credentials["twilio_auth_token"]
            if credentials is not None
            else current_app.config.get("TWILIO_AUTH_TOKEN")
        )
        sender = (
            (credentials["twilio_messaging_service_sid"] or credentials["twilio_from_number"])
            if credentials is not None
            else current_app.config.get("TWILIO_MESSAGING_SERVICE_SID")
            or current_app.config.get("TWILIO_FROM_NUMBER")
        )
        return bool(account_sid and auth_token and sender)
    if provider == "africastalking":
        api_key = (
            credentials["at_api_key"]
            if credentials is not None
            else current_app.config.get("AT_API_KEY")
        )
        username = (
            credentials["at_username"]
            if credentials is not None
            else current_app.config.get("AT_USERNAME")
        )
        return bool(api_key and username)
    return False


def _send_sms_africas_talking(phone_number, message, credentials=None):
    if not _credentials_present_sms(credentials):
        return False, "Africa's Talking SMS is not configured"

    api_key = (
        credentials["at_api_key"]
        if credentials is not None
        else current_app.config["AT_API_KEY"]
    )
    username = (
        credentials["at_username"]
        if credentials is not None
        else current_app.config["AT_USERNAME"]
    )
    url = "https://api.africastalking.com/version1/messaging"
    headers = {
        "apiKey": api_key,
        "Content-Type": "application/x-www-form-urlencoded",
        "Accept": "application/json",
    }
    data = {
        "username": username,
        "to": phone_number,
        "message": message,
    }
    # Africa's Talking uses `from` as an alphanumeric sender ID, so only pass it
    # when the retailer set one. Recipients still see the shop's own identity.
    from_number = (
        credentials.get("reminder_sender_number", "")
        if credentials is not None
        else ""
    )
    if from_number:
        data["from"] = from_number
    try:
        response = requests.post(url, headers=headers, data=data, timeout=10)
        response.raise_for_status()
        try:
            payload = response.json()
        except ValueError:
            return False, "Africa's Talking returned an unreadable SMS response"

        sms_data = payload.get("SMSMessageData", {})
        recipients = sms_data.get("Recipients", [])
        if recipients:
            failed = [
                recipient
                for recipient in recipients
                if recipient.get("statusCode") not in (101, 102)
            ]
            if failed:
                status = failed[0].get("status") or sms_data.get("Message")
                status_code = failed[0].get("statusCode", "unknown")
                return False, f"Africa's Talking rejected the recipient (status {status_code}): {status}"
            return True, sms_data.get("Message", "SMS accepted by Africa's Talking")

        error = payload.get("errorMessage") or sms_data.get("Message")
        if error:
            return False, f"Africa's Talking rejected the SMS: {error}"
        return False, "Africa's Talking response did not confirm a recipient"
    except requests.RequestException as exc:
        return False, _provider_request_error(exc, "Africa's Talking")


def _send_sms_twilio(phone_number, message, credentials=None):
    account_sid = (
        credentials["twilio_account_sid"]
        if credentials is not None
        else current_app.config.get("TWILIO_ACCOUNT_SID")
    )
    auth_token = (
        credentials["twilio_auth_token"]
        if credentials is not None
        else current_app.config.get("TWILIO_AUTH_TOKEN")
    )
    messaging_service_sid = (
        credentials["twilio_messaging_service_sid"]
        if credentials is not None
        else current_app.config.get("TWILIO_MESSAGING_SERVICE_SID")
    )
    from_number = (
        credentials["twilio_from_number"]
        if credentials is not None
        else current_app.config.get("TWILIO_FROM_NUMBER")
    )
    # A retailer's own reminder number is the preferred sender for this shop;
    # an explicitly configured Twilio sender number still wins.
    if credentials is not None:
        from_number = from_number or credentials.get("reminder_sender_number")
    if not account_sid or not auth_token or not (messaging_service_sid or from_number):
        return False, (
            "Twilio is selected but its account credentials or sender are missing. "
            "Set the shop reminder number in messaging settings."
        )

    url = f"https://api.twilio.com/2010-04-01/Accounts/{account_sid}/Messages.json"
    data = {"To": phone_number, "Body": message}
    if messaging_service_sid:
        data["MessagingServiceSid"] = messaging_service_sid
    else:
        data["From"] = from_number
    try:
        response = requests.post(
            url,
            data=data,
            auth=(account_sid, auth_token),
            timeout=10,
        )
        response.raise_for_status()
        return True, "Message accepted by Twilio"
    except requests.RequestException as exc:
        return False, _provider_request_error(exc, "Twilio")


def _credentials_present_whatsapp(credentials=None):
    token = (
        credentials["whatsapp_token"]
        if credentials is not None
        else current_app.config.get("WHATSAPP_TOKEN")
    )
    phone_id = (
        credentials["whatsapp_phone_id"]
        if credentials is not None
        else current_app.config.get("WHATSAPP_PHONE_ID")
    )
    return bool(token and phone_id)


def send_sms(phone_number, message, retailer_id=None):
    """Send through the configured SMS provider."""
    try:
        credentials = (
            load_retailer_credentials(retailer_id)
            if retailer_id is not None
            else None
        )
    except MessagingCredentialError as exc:
        return False, str(exc)

    provider = (
        credentials["sms_provider"].lower()
        if credentials is not None
        else current_app.config.get("SMS_PROVIDER", "africastalking").lower()
    )
    if provider == "twilio":
        return _send_sms_twilio(phone_number, message, credentials)
    if provider == "africastalking":
        return _send_sms_africas_talking(phone_number, message, credentials)
    return False, f"Unsupported SMS_PROVIDER: {provider}"


def send_whatsapp(phone_number, message, retailer_id=None):
    """Send via Meta WhatsApp Business Cloud API. Returns (success: bool, info: str)."""
    try:
        credentials = (
            load_retailer_credentials(retailer_id)
            if retailer_id is not None
            else None
        )
    except MessagingCredentialError as exc:
        return False, str(exc)
    if not _credentials_present_whatsapp(credentials):
        return False, "WhatsApp is not configured"

    phone_id = (
        credentials["whatsapp_phone_id"]
        if credentials is not None
        else current_app.config["WHATSAPP_PHONE_ID"]
    )
    token = (
        credentials["whatsapp_token"]
        if credentials is not None
        else current_app.config["WHATSAPP_TOKEN"]
    )
    url = f"https://graph.facebook.com/v19.0/{phone_id}/messages"
    headers = {
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
    }
    payload = {
        "messaging_product": "whatsapp",
        "to": re.sub(r"\D", "", phone_number),
        "type": "text",
        "text": {"body": message},
    }
    # The Cloud API accepts a business phone number ID as `from` when a retailer
    # registered their own number; without one Meta uses the default sender.
    sender_number = (
        credentials.get("reminder_sender_number", "")
        if credentials is not None
        else ""
    )
    if sender_number:
        payload["from"] = re.sub(r"\D", "", sender_number)
    try:
        resp = requests.post(url, headers=headers, json=payload, timeout=10)
        resp.raise_for_status()
        return True, "Message accepted by WhatsApp"
    except requests.RequestException as exc:
        return False, _provider_request_error(exc, "WhatsApp")


def _provider_request_error(exc, provider):
    response = getattr(exc, "response", None)
    if response is not None:
        try:
            payload = response.json()
        except ValueError:
            payload = {}
        if not isinstance(payload, dict):
            payload = {}
        if provider == "WhatsApp":
            provider_error = payload.get("error")
            detail = (
                provider_error.get("message")
                if isinstance(provider_error, dict)
                else provider_error
            )
        elif provider == "Twilio":
            detail = payload.get("message")
        else:
            detail = payload.get("errorMessage")

        if detail:
            return f"{provider} rejected the request: {str(detail)[:400]}"
        return f"{provider} request failed with HTTP {response.status_code}"

    return f"{provider} request failed: {type(exc).__name__}"


def dispatch_reminder(customer, rule, message):
    """Send and log reminders on the rule's channel(s) or customer's preference."""
    channel = rule.channel if rule.channel != "both" else customer.preferred_channel
    channels = ("sms", "whatsapp") if channel == "both" else (channel,)
    return _dispatch_channels(customer, rule.rule_id, channels, message)


def dispatch_personal_reminder(customer, message, rule_id=None, channel=None):
    """Send a reminder using the saved preference or an explicitly selected channel."""
    channel = channel or customer.preferred_channel
    channels = ("sms", "whatsapp") if channel == "both" else (channel,)
    return _dispatch_channels(customer, rule_id, channels, message)


def provider_configuration(retailer_id=None):
    """Expose provider readiness without returning any credential values."""
    sender_number = ""
    if retailer_id is not None:
        settings = MessagingSettings.query.filter_by(retailer_id=retailer_id).first()
        provider = settings.sms_provider if settings else "africastalking"
        sender_number = (settings.reminder_sender_number or "") if settings else ""
        try:
            credentials = load_retailer_credentials(retailer_id)
            sms_ready = _credentials_present_sms(credentials)
            whatsapp_ready = _credentials_present_whatsapp(credentials)
            error = None
        except MessagingCredentialError as exc:
            sms_ready = False
            whatsapp_ready = False
            error = str(exc)
    else:
        provider = current_app.config.get("SMS_PROVIDER", "africastalking").lower()
        sms_ready = _credentials_present_sms()
        whatsapp_ready = _credentials_present_whatsapp()
        error = None

    providers = {
        "sms": {
            "provider": provider,
            "configured": sms_ready,
            "sender_number": sender_number,
        },
        "whatsapp": {
            "provider": "meta_whatsapp_cloud",
            "configured": whatsapp_ready,
            "sender_number": sender_number,
        },
    }
    if error:
        providers["configuration_error"] = error
    return providers


def _dispatch_channels(customer, rule_id, channels, message):
    results = []

    for channel in channels:
        if channel == "whatsapp":
            success, info = send_whatsapp(
                customer.phone_number, message, retailer_id=customer.retailer_id
            )
        else:
            success, info = send_sms(
                customer.phone_number, message, retailer_id=customer.retailer_id
            )

        results.append((success, info))
        db.session.add(
            MessageDispatchLog(
                customer_id=customer.customer_id,
                rule_id=rule_id,
                channel=channel,
                message_content=message,
                status="sent" if success else "failed",
                failure_reason=None if success else info[:500],
                sent_at=datetime.utcnow(),
            )
        )

    db.session.commit()
    return (
        all(result[0] for result in results),
        "; ".join(result[1] for result in results),
    )
