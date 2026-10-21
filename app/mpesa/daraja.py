"""Safaricom Daraja API: OAuth, STK Push (Lipa Na M-Pesa Online) and STK status query.

https://developer.safaricom.co.ke/APIs/MpesaExpressSimulate
"""

import base64
import re
from datetime import datetime

import requests
from flask import current_app

from app.reminders.credentials import decrypt_secret
from app.timezones import NAIROBI_TIMEZONE

BASE_URLS = {
    "sandbox": "https://sandbox.safaricom.co.ke",
    "production": "https://api.safaricom.co.ke",
}

# STK result codes worth explaining to the retailer.
RESULT_MESSAGES = {
    "0": "Paid",
    "1": "The customer's M-Pesa balance is too low for this payment.",
    "1032": "The customer cancelled the prompt.",
    "1037": "The customer's phone could not be reached or did not respond in time.",
    "2001": "The customer entered the wrong M-Pesa PIN.",
}
CANCELLED_CODES = {"1032", "1037"}
# Returned by the status query while the customer has not answered yet.
STILL_PROCESSING_CODE = "500.001.1001"


class DarajaError(Exception):
    """Daraja rejected a request or could not be reached. The message is safe to show."""


def safaricom_msisdn(phone_number):
    """Return 2547XXXXXXXX / 2541XXXXXXXX for a Kenyan mobile number, else None."""
    digits = re.sub(r"\D", "", phone_number or "")
    if digits.startswith("0"):
        digits = "254" + digits[1:]
    elif len(digits) == 9 and digits[0] in "71":
        digits = "254" + digits
    return digits if re.fullmatch(r"254[71]\d{8}", digits) else None


def account_reference_for(shop_name):
    """The shop name as Daraja's 12-character account reference shown in the prompt."""
    cleaned = re.sub(r"[^A-Za-z0-9 ]", "", shop_name or "").strip().upper()
    return (cleaned[:12].strip() or "DUKA CREDIT")


def _timestamp():
    return datetime.now(NAIROBI_TIMEZONE).strftime("%Y%m%d%H%M%S")


class DarajaClient:
    def __init__(self, settings):
        self.settings = settings
        self.base_url = BASE_URLS[settings.environment]
        self.timeout = current_app.config.get("MPESA_REQUEST_TIMEOUT_SECONDS", 30)

    def _access_token(self):
        try:
            response = requests.get(
                f"{self.base_url}/oauth/v1/generate",
                params={"grant_type": "client_credentials"},
                auth=(
                    decrypt_secret(self.settings.consumer_key_encrypted),
                    decrypt_secret(self.settings.consumer_secret_encrypted),
                ),
                timeout=self.timeout,
            )
        except requests.RequestException as exc:
            raise DarajaError("Could not reach Safaricom. Check the internet connection.") from exc
        if response.status_code != 200:
            raise DarajaError(
                "Safaricom refused the consumer key and secret. Check them on the M-Pesa page "
                "and that they match the sandbox or production setting."
            )
        token = response.json().get("access_token")
        if not token:
            raise DarajaError("Safaricom did not return an access token.")
        return token

    def _password(self, timestamp):
        passkey = decrypt_secret(self.settings.passkey_encrypted)
        raw = f"{self.settings.business_shortcode}{passkey}{timestamp}"
        return base64.b64encode(raw.encode("utf-8")).decode("ascii")

    def _post(self, path, payload):
        token = self._access_token()
        try:
            response = requests.post(
                f"{self.base_url}{path}",
                json=payload,
                headers={"Authorization": f"Bearer {token}"},
                timeout=self.timeout,
            )
        except requests.RequestException as exc:
            raise DarajaError("Could not reach Safaricom. Check the internet connection.") from exc
        try:
            return response.status_code, response.json()
        except ValueError as exc:
            raise DarajaError("Safaricom sent an unexpected response.") from exc

    def stk_push(self, phone_number, amount, account_reference, description, callback_url):
        """Send the payment prompt. Returns Daraja's response with the CheckoutRequestID."""
        timestamp = _timestamp()
        till = self.settings.account_type == "till"
        status, body = self._post(
            "/mpesa/stkpush/v1/processrequest",
            {
                "BusinessShortCode": self.settings.business_shortcode,
                "Password": self._password(timestamp),
                "Timestamp": timestamp,
                "TransactionType": "CustomerBuyGoodsOnline" if till else "CustomerPayBillOnline",
                "Amount": int(amount),
                "PartyA": phone_number,
                "PartyB": self.settings.receiving_number,
                "PhoneNumber": phone_number,
                "CallBackURL": callback_url,
                "AccountReference": account_reference,
                "TransactionDesc": description[:13],
            },
        )
        if status != 200 or str(body.get("ResponseCode")) != "0":
            message = body.get("errorMessage") or body.get("ResponseDescription") or "Unknown error"
            current_app.logger.warning("STK push rejected (%s): %s", status, message)
            raise DarajaError(f"Safaricom did not send the prompt: {message}")
        return body

    def stk_query(self, checkout_request_id):
        """Ask Safaricom how a prompt ended. Returns (result_code or None if still waiting, description)."""
        timestamp = _timestamp()
        status, body = self._post(
            "/mpesa/stkpushquery/v1/query",
            {
                "BusinessShortCode": self.settings.business_shortcode,
                "Password": self._password(timestamp),
                "Timestamp": timestamp,
                "CheckoutRequestID": checkout_request_id,
            },
        )
        if body.get("errorCode") == STILL_PROCESSING_CODE:
            return None, "The customer has not answered the prompt yet."
        if "ResultCode" not in body:
            message = body.get("errorMessage") or "Unknown error"
            raise DarajaError(f"Safaricom could not report the status: {message}")
        return str(body["ResultCode"]), body.get("ResultDesc", "")
