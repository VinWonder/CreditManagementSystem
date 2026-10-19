"""
Message dispatch service.

Sends reminders via SMS (Africa's Talking - recommended for Kenya: cheap,
reliable local delivery, simple REST API) or WhatsApp (Meta WhatsApp
Business Cloud API - free-tier friendly, rich formatting, read receipts).

Both integrations degrade gracefully in development: if API credentials are
not configured, messages are logged to MessageDispatchLog with status
'sent' (simulated) so the rest of the system can be demoed/tested without
live credit-consuming API calls.
"""
import requests
from flask import current_app
from datetime import datetime

from app.extensions import db
from app.models import MessageDispatchLog


def _credentials_present_sms():
    return bool(current_app.config.get("AT_API_KEY"))


def _send_sms_africas_talking(phone_number, message):
    if not _credentials_present_sms():
        return True, "simulated (no AT_API_KEY configured)"

    url = "https://api.africastalking.com/version1/messaging"
    headers = {
        "apiKey": current_app.config["AT_API_KEY"],
        "Content-Type": "application/x-www-form-urlencoded",
        "Accept": "application/json",
    }
    data = {
        "username": current_app.config["AT_USERNAME"],
        "to": phone_number,
        "message": message,
    }
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
        return False, str(exc)


def _send_sms_twilio(phone_number, message):
    account_sid = current_app.config.get("TWILIO_ACCOUNT_SID")
    auth_token = current_app.config.get("TWILIO_AUTH_TOKEN")
    messaging_service_sid = current_app.config.get("TWILIO_MESSAGING_SERVICE_SID")
    from_number = current_app.config.get("TWILIO_FROM_NUMBER")
    if not account_sid or not auth_token or not (messaging_service_sid or from_number):
        return False, "Twilio is selected but its account credentials or sender are missing"

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
        return True, response.text
    except requests.RequestException as exc:
        return False, str(exc)


def _credentials_present_whatsapp():
    return bool(current_app.config.get("WHATSAPP_TOKEN")) and bool(
        current_app.config.get("WHATSAPP_PHONE_ID")
    )


def send_sms(phone_number, message):
    """Send through the configured SMS provider."""
    provider = current_app.config.get("SMS_PROVIDER", "africastalking").lower()
    if provider == "twilio":
        return _send_sms_twilio(phone_number, message)
    if provider == "africastalking":
        return _send_sms_africas_talking(phone_number, message)
    return False, f"Unsupported SMS_PROVIDER: {provider}"


def send_whatsapp(phone_number, message):
    """Send via Meta WhatsApp Business Cloud API. Returns (success: bool, info: str)."""
    if not _credentials_present_whatsapp():
        return True, "simulated (no WHATSAPP_TOKEN configured)"

    phone_id = current_app.config["WHATSAPP_PHONE_ID"]
    url = f"https://graph.facebook.com/v19.0/{phone_id}/messages"
    headers = {
        "Authorization": f"Bearer {current_app.config['WHATSAPP_TOKEN']}",
        "Content-Type": "application/json",
    }
    payload = {
        "messaging_product": "whatsapp",
        "to": phone_number,
        "type": "text",
        "text": {"body": message},
    }
    try:
        resp = requests.post(url, headers=headers, json=payload, timeout=10)
        resp.raise_for_status()
        return True, resp.text
    except requests.RequestException as exc:
        return False, str(exc)


def dispatch_reminder(customer, rule, message):
    """Send and log reminders on the rule's channel(s) or customer's preference."""
    channel = rule.channel if rule.channel != "both" else customer.preferred_channel
    channels = ("sms", "whatsapp") if channel == "both" else (channel,)
    return _dispatch_channels(customer, rule.rule_id, channels, message)


def dispatch_personal_reminder(customer, message):
    """Send an on-demand reminder using the customer's saved channel preference."""
    channel = customer.preferred_channel
    channels = ("sms", "whatsapp") if channel == "both" else (channel,)
    return _dispatch_channels(customer, None, channels, message)


def _dispatch_channels(customer, rule_id, channels, message):
    results = []

    for channel in channels:
        if channel == "whatsapp":
            success, info = send_whatsapp(customer.phone_number, message)
        else:
            success, info = send_sms(customer.phone_number, message)

        results.append((success, info))
        db.session.add(
            MessageDispatchLog(
                customer_id=customer.customer_id,
                rule_id=rule_id,
                channel=channel,
                message_content=message,
                status="sent" if success else "failed",
                sent_at=datetime.utcnow(),
            )
        )

    db.session.commit()
    return (
        all(result[0] for result in results),
        "; ".join(result[1] for result in results),
    )
