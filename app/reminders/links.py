"""Retailer-initiated reminder links.

Reminders are not sent by the server. The retailer's own registered phone
number is the sender: the dashboard composes the configured message and offers
WhatsApp and Kenyan SMS links, which open the retailer's own messaging app with
the customer's number and the message prefilled. The retailer presses send.
"""

import re
from urllib.parse import quote

# Telkom / Airtel Kenya and Safaricom all accept the international format, so a
# single E.164 number works from any handset. Kenyan numbers are +254XXXXXXXXX.
KENYA_COUNTRY_CODE = "254"


def normalize_msisdn(phone_number):
    """Return digits only, assuming Kenya when no country code is present.

    Returns an empty string when the number cannot be used for a link.
    """
    digits = re.sub(r"\D", "", phone_number or "")
    if not digits:
        return ""
    if digits.startswith("00"):
        digits = digits[2:]
    elif digits.startswith("0"):
        digits = KENYA_COUNTRY_CODE + digits[1:]
    elif digits[:1] in {"7", "1"}:
        # Local mobile format pasted without a leading zero, e.g. 712345678.
        digits = KENYA_COUNTRY_CODE + digits
    if len(digits) < 9 or len(digits) > 15:
        return ""
    return digits


def whatsapp_link(phone_number, message):
    """Open WhatsApp chat with the customer, message prefilled."""
    msisdn = normalize_msisdn(phone_number)
    if not msisdn:
        return None
    return f"https://wa.me/{msisdn}?text={quote(message, safe='')}"


def sms_link(phone_number, message):
    """Open the phone's SMS composer. `?body=` is honoured on Android and iOS."""
    msisdn = normalize_msisdn(phone_number)
    if not msisdn:
        return None
    return f"sms:+{msisdn}?body={quote(message, safe='')}"


def allowed_channels(customer):
    """The channels the customer chose to be contacted on: sms, whatsapp, or both."""
    preference = (customer.preferred_channel or "sms").lower()
    if preference == "both":
        return ("whatsapp", "sms")
    return (preference,) if preference in {"sms", "whatsapp"} else ("sms",)


def reminder_links(customer, message):
    """Links for the customer's chosen channels only; None where the number is unusable.

    A channel the customer did not choose is left out entirely, so the page
    never offers it.
    """
    builders = {"whatsapp": whatsapp_link, "sms": sms_link}
    return {
        channel: builders[channel](customer.phone_number, message)
        for channel in allowed_channels(customer)
    }
