"""Safaricom Daraja API: OAuth, STK Push (Lipa Na M-Pesa Online) and STK status query.

https://developer.safaricom.co.ke/APIs/MpesaExpressSimulate
"""

import base64
import re
import time
from datetime import datetime

import requests
from flask import current_app

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
# Returned by the status query while the customer has not answered yet: an
# error code before Safaricom has the request, then result code 4999.
STILL_PROCESSING_CODE = "500.001.1001"
STILL_PROCESSING_RESULT = "4999"


# Access tokens last about an hour; reuse them instead of signing in on every
# call, which doubles traffic and trips Safaricom's rate limit.
_token_cache = {}
TOKEN_REFRESH_MARGIN_SECONDS = 60


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


def missing_mpesa_settings(config):
    """Names of required MPESA_* settings that are empty or invalid; [] when ready."""
    missing = [
        name
        for name in ("MPESA_SHORTCODE", "MPESA_CONSUMER_KEY", "MPESA_CONSUMER_SECRET", "MPESA_PASSKEY")
        if not config.get(name)
    ]
    if config.get("MPESA_ENVIRONMENT") not in BASE_URLS:
        missing.append("MPESA_ENVIRONMENT")
    if config.get("MPESA_ACCOUNT_TYPE") not in {"paybill", "till"}:
        missing.append("MPESA_ACCOUNT_TYPE")
    elif config.get("MPESA_ACCOUNT_TYPE") == "till" and not config.get("MPESA_TILL_NUMBER"):
        missing.append("MPESA_TILL_NUMBER")
    return missing


def stk_push_enabled():
    return not missing_mpesa_settings(current_app.config)


class DarajaClient:
    def __init__(self, config=None):
        config = config or current_app.config
        missing = missing_mpesa_settings(config)
        if missing:
            raise DarajaError(
                "M-Pesa prompts are not set up on this server. Fill in " + ", ".join(missing) + " in .env."
            )
        self.environment = config["MPESA_ENVIRONMENT"]
        self.base_url = BASE_URLS[self.environment]
        self.till = config["MPESA_ACCOUNT_TYPE"] == "till"
        self.shortcode = config["MPESA_SHORTCODE"]
        # Customers pay to the Paybill, or for Buy Goods to the till under the store number.
        self.receiving_number = config["MPESA_TILL_NUMBER"] if self.till else self.shortcode
        self._key = config["MPESA_CONSUMER_KEY"]
        self._secret = config["MPESA_CONSUMER_SECRET"]
        self._passkey = config["MPESA_PASSKEY"]
        self.timeout = config.get("MPESA_REQUEST_TIMEOUT_SECONDS", 30)

    def _access_token(self):
        cache_key = (self.base_url, self._key, self._secret)
        cached = _token_cache.get(cache_key)
        if cached and cached[1] > time.monotonic():
            return cached[0]
        try:
            response = requests.get(
                f"{self.base_url}/oauth/v1/generate",
                params={"grant_type": "client_credentials"},
                auth=(self._key, self._secret),
                timeout=self.timeout,
            )
        except requests.RequestException as exc:
            raise DarajaError("Could not reach Safaricom. Check the internet connection.") from exc
        if response.status_code in (400, 401):
            raise DarajaError(
                "Safaricom refused the consumer key and secret. Check MPESA_CONSUMER_KEY and "
                "MPESA_CONSUMER_SECRET in .env and that they match MPESA_ENVIRONMENT."
            )
        try:
            body = response.json() if response.status_code == 200 else {}
        except ValueError:
            body = {}
        token = body.get("access_token")
        if not token:
            # Rate limits and outages, not bad keys.
            raise DarajaError("Safaricom is busy right now. Please try again in a minute.")
        lifetime = int(body.get("expires_in") or 3599)
        _token_cache[cache_key] = (
            token,
            time.monotonic() + max(lifetime - TOKEN_REFRESH_MARGIN_SECONDS, 0),
        )
        return token

    def _password(self, timestamp):
        raw = f"{self.shortcode}{self._passkey}{timestamp}"
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
            # Safaricom answers rate limits and outages with a non-JSON page.
            raise DarajaError("Safaricom is busy right now. Please try again in a minute.") from exc

    def stk_push(self, phone_number, amount, account_reference, description, callback_url):
        """Send the payment prompt. Returns Daraja's response with the CheckoutRequestID."""
        timestamp = _timestamp()
        status, body = self._post(
            "/mpesa/stkpush/v1/processrequest",
            {
                "BusinessShortCode": self.shortcode,
                "Password": self._password(timestamp),
                "Timestamp": timestamp,
                "TransactionType": "CustomerBuyGoodsOnline" if self.till else "CustomerPayBillOnline",
                "Amount": int(amount),
                "PartyA": phone_number,
                "PartyB": self.receiving_number,
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
                "BusinessShortCode": self.shortcode,
                "Password": self._password(timestamp),
                "Timestamp": timestamp,
                "CheckoutRequestID": checkout_request_id,
            },
        )
        if (
            body.get("errorCode") == STILL_PROCESSING_CODE
            or str(body.get("ResultCode")) == STILL_PROCESSING_RESULT
        ):
            return None, "The customer has not answered the prompt yet."
        if "ResultCode" not in body:
            message = body.get("errorMessage") or "Unknown error"
            raise DarajaError(f"Safaricom could not report the status: {message}")
        return str(body["ResultCode"]), body.get("ResultDesc", "")
