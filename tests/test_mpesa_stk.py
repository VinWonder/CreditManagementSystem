import base64
import unittest
from datetime import datetime, timedelta
from decimal import Decimal
from unittest.mock import MagicMock, patch

from cryptography.fernet import Fernet
from flask import g
from flask_login import login_user

from app import create_app
from app.extensions import db
from app.models import (
    CreditTransaction,
    Customer,
    Ledger,
    MpesaSettings,
    Payment,
    Retailer,
    Shopkeeper,
    StkPushRequest,
)
from app.mpesa.daraja import account_reference_for, safaricom_msisdn
from app.reminders.credentials import decrypt_secret, encrypt_secret


class MpesaConfig:
    APP_ENV = "testing"
    SECRET_KEY = "test-secret"
    SQLALCHEMY_DATABASE_URI = "sqlite://"
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    SQLALCHEMY_ENGINE_OPTIONS = {}
    AUTO_CREATE_DB = False
    WTF_CSRF_ENABLED = False
    DEFAULT_PHONE_REGION = "KE"
    MESSAGING_CREDENTIALS_ENCRYPTION_KEY = Fernet.generate_key().decode()
    PUBLIC_BASE_URL = "https://duka.example.com"
    MPESA_REQUEST_TIMEOUT_SECONDS = 5


def fake_response(status, body):
    response = MagicMock(status_code=status)
    response.json.return_value = body
    return response


TOKEN = fake_response(200, {"access_token": "test-token", "expires_in": "3599"})
STK_ACCEPTED = fake_response(
    200,
    {
        "MerchantRequestID": "29115-34620561-1",
        "CheckoutRequestID": "ws_CO_191220191020363925",
        "ResponseCode": "0",
        "ResponseDescription": "Success. Request accepted for processing",
        "CustomerMessage": "Success. Request accepted for processing",
    },
)


class HelperTests(unittest.TestCase):
    def test_safaricom_numbers_are_normalised(self):
        for raw in ("+254712345678", "0712345678", "712345678", "254 712 345 678"):
            self.assertEqual(safaricom_msisdn(raw), "254712345678")
        self.assertEqual(safaricom_msisdn("0112345678"), "254112345678")
        self.assertIsNone(safaricom_msisdn("+255712345678"))
        self.assertIsNone(safaricom_msisdn("0212345678"))

    def test_account_reference_is_the_shop_name_in_twelve_characters(self):
        self.assertEqual(account_reference_for("Mama Njeri Shop"), "MAMA NJERI S")
        self.assertEqual(account_reference_for("Kiosk"), "KIOSK")
        self.assertEqual(account_reference_for("!!!"), "DUKA CREDIT")


class StkPushTests(unittest.TestCase):
    def setUp(self):
        self.app = create_app(MpesaConfig)
        self.app_context = self.app.app_context()
        self.app_context.push()
        db.create_all()
        self.client = self.app.test_client()

        self.retailer = Retailer(
            business_name="Mama Njeri Shop",
            owner_name="Njeri",
            phone_number="+254700000031",
            password_hash="x",
        )
        db.session.add(self.retailer)
        db.session.flush()
        self.customer = Customer(
            retailer_id=self.retailer.retailer_id,
            full_name="Peter Kamau",
            phone_number="+254712345678",
            accepts_stk_prompts=True,
        )
        db.session.add(self.customer)
        db.session.flush()
        self.shopkeeper = Shopkeeper(
            retailer_id=self.retailer.retailer_id,
            full_name="Clerk",
            phone_number="+254700000032",
            password_hash="x",
        )
        db.session.add_all(
            [
                self.shopkeeper,
                CreditTransaction(
                    retailer_id=self.retailer.retailer_id,
                    customer_id=self.customer.customer_id,
                    item_description="Sugar",
                    amount=Decimal("500.50"),
                    transaction_date=datetime(2026, 10, 1),
                ),
                Ledger(
                    retailer_id=self.retailer.retailer_id,
                    customer_id=self.customer.customer_id,
                    outstanding_balance=Decimal("500.50"),
                ),
            ]
        )
        db.session.commit()
        self.user = self.retailer

        @self.app.before_request
        def authenticate():
            g.pop("_login_user", None)
            login_user(self.user)

        patcher = patch("app.mpesa.daraja.requests")
        self.requests = patcher.start()
        self.requests.RequestException = Exception
        self.addCleanup(patcher.stop)
        self.requests.get.return_value = TOKEN
        self.requests.post.return_value = STK_ACCEPTED

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.app_context.pop()

    def _connect(self, account_type="paybill"):
        db.session.add(
            MpesaSettings(
                retailer_id=self.retailer.retailer_id,
                environment="sandbox",
                account_type=account_type,
                business_shortcode="174379",
                till_number="123456" if account_type == "till" else None,
                consumer_key_encrypted=encrypt_secret("key"),
                consumer_secret_encrypted=encrypt_secret("secret"),
                passkey_encrypted=encrypt_secret("passkey"),
            )
        )
        db.session.commit()

    def _prompt(self, amount="500", phone="0712345678"):
        return self.client.post(
            f"/mpesa/customers/{self.customer.customer_id}/prompt",
            data={"amount": amount, "phone_number": phone},
        )

    def _callback(self, stk, result_code=0, receipt="QFT4ABC123", amount=500):
        body = {
            "Body": {
                "stkCallback": {
                    "MerchantRequestID": stk.merchant_request_id,
                    "CheckoutRequestID": stk.checkout_request_id,
                    "ResultCode": result_code,
                    "ResultDesc": "The service request is processed successfully.",
                }
            }
        }
        if result_code == 0:
            body["Body"]["stkCallback"]["CallbackMetadata"] = {
                "Item": [
                    {"Name": "Amount", "Value": amount},
                    {"Name": "MpesaReceiptNumber", "Value": receipt},
                    {"Name": "TransactionDate", "Value": 20261021103000},
                    {"Name": "PhoneNumber", "Value": 254712345678},
                ]
            }
        return self.client.post(f"/mpesa/callback/{stk.callback_token}", json=body)

    # Settings

    def test_settings_page_saves_encrypted_keys(self):
        response = self.client.post(
            "/mpesa/settings",
            data={
                "environment": "sandbox",
                "account_type": "paybill",
                "business_shortcode": "174379",
                "consumer_key": "my-key",
                "consumer_secret": "my-secret",
                "passkey": "my-passkey",
            },
        )

        self.assertEqual(response.status_code, 302)
        saved = MpesaSettings.query.one()
        self.assertNotIn("my-secret", saved.consumer_secret_encrypted)
        self.assertEqual(decrypt_secret(saved.consumer_secret_encrypted), "my-secret")
        page = self.client.get("/mpesa/settings").get_data(as_text=True)
        self.assertNotIn("my-secret", page)
        self.assertIn("Connected (sandbox)", page)
        self.assertIn("MAMA NJERI S", page)

    def test_blank_secrets_keep_the_saved_ones(self):
        self._connect()

        self.client.post(
            "/mpesa/settings",
            data={"environment": "production", "account_type": "paybill", "business_shortcode": "600100"},
        )

        saved = MpesaSettings.query.one()
        self.assertEqual(saved.environment, "production")
        self.assertEqual(decrypt_secret(saved.passkey_encrypted), "passkey")

    def test_till_needs_a_till_number(self):
        response = self.client.post(
            "/mpesa/settings",
            data={
                "environment": "sandbox",
                "account_type": "till",
                "business_shortcode": "174379",
                "consumer_key": "k",
                "consumer_secret": "s",
                "passkey": "p",
            },
        )

        self.assertIn(b"Enter the Till number customers pay to.", response.data)
        self.assertEqual(MpesaSettings.query.count(), 0)

    def test_shopkeepers_cannot_use_m_pesa_prompts(self):
        self._connect()
        self.user = self.shopkeeper

        self.assertEqual(self.client.get("/mpesa/settings").status_code, 403)
        self.assertEqual(self._prompt().status_code, 403)
        page = self.client.get(f"/customers/{self.customer.customer_id}").get_data(as_text=True)
        self.assertNotIn("Request payment by M-Pesa", page)

    # Sending

    def test_prompt_is_sent_with_shop_name_amount_and_paybill(self):
        self._connect()

        response = self._prompt("500")

        self.assertEqual(response.status_code, 302)
        payload = self.requests.post.call_args.kwargs["json"]
        self.assertEqual(payload["BusinessShortCode"], "174379")
        self.assertEqual(payload["TransactionType"], "CustomerPayBillOnline")
        self.assertEqual(payload["Amount"], 500)
        self.assertEqual(payload["PartyA"], "254712345678")
        self.assertEqual(payload["PartyB"], "174379")
        self.assertEqual(payload["PhoneNumber"], "254712345678")
        self.assertEqual(payload["AccountReference"], "MAMA NJERI S")
        self.assertTrue(payload["CallBackURL"].startswith("https://duka.example.com/mpesa/callback/"))
        password = base64.b64decode(payload["Password"]).decode()
        self.assertEqual(password, f"174379passkey{payload['Timestamp']}")
        stk = StkPushRequest.query.one()
        self.assertEqual(stk.status, "pending")
        self.assertEqual(stk.checkout_request_id, "ws_CO_191220191020363925")
        self.assertIn(stk.callback_token, payload["CallBackURL"])

    def test_till_prompts_use_buy_goods(self):
        self._connect("till")

        self._prompt("100")

        payload = self.requests.post.call_args.kwargs["json"]
        self.assertEqual(payload["TransactionType"], "CustomerBuyGoodsOnline")
        self.assertEqual(payload["PartyB"], "123456")

    def test_balance_with_cents_rounds_up_but_not_beyond(self):
        self._connect()

        self._prompt("502")
        self.assertEqual(StkPushRequest.query.count(), 0)
        self._prompt("501")
        self.assertEqual(StkPushRequest.query.count(), 1)

    def test_customer_must_have_agreed(self):
        self._connect()
        self.customer.accepts_stk_prompts = False
        db.session.commit()

        self._prompt()

        self.requests.post.assert_not_called()
        self.assertEqual(StkPushRequest.query.count(), 0)

    def test_settings_are_needed_first(self):
        response = self._prompt()

        self.assertTrue(response.headers["Location"].endswith("/mpesa/settings"))
        self.requests.post.assert_not_called()

    def test_non_safaricom_number_is_refused(self):
        self._connect()

        self._prompt(phone="+255712345678")

        self.requests.post.assert_not_called()

    def test_a_second_prompt_waits_for_the_first(self):
        self._connect()
        self._prompt("100")
        self.requests.post.return_value = fake_response(
            200, dict(STK_ACCEPTED.json.return_value, CheckoutRequestID="ws_CO_second")
        )

        self._prompt("100")

        self.assertEqual(StkPushRequest.query.count(), 1)

    def test_safaricom_errors_are_shown_and_nothing_is_saved(self):
        self._connect()
        self.requests.post.return_value = fake_response(
            400, {"errorCode": "400.002.02", "errorMessage": "Bad Request - Invalid PhoneNumber"}
        )

        self._prompt()
        page = self.client.get(f"/customers/{self.customer.customer_id}").get_data(as_text=True)

        self.assertEqual(StkPushRequest.query.count(), 0)
        self.assertIn("Invalid PhoneNumber", page)

    def test_wrong_keys_are_explained(self):
        self._connect()
        self.requests.get.return_value = fake_response(400, {})

        self._prompt()
        page = self.client.get(f"/customers/{self.customer.customer_id}").get_data(as_text=True)

        self.assertIn("refused the consumer key and secret", page)

    # Results

    def test_paid_callback_records_an_approved_payment(self):
        self._connect()
        self._prompt("500")
        stk = StkPushRequest.query.one()

        response = self._callback(stk)

        self.assertEqual(response.get_json(), {"ResultCode": 0, "ResultDesc": "Accepted"})
        payment = Payment.query.one()
        self.assertEqual(payment.amount_paid, Decimal("500.00"))
        self.assertEqual(payment.approval_status, "approved")
        self.assertEqual(payment.payment_method, "mpesa_paybill")
        self.assertEqual(payment.payment_reference, "QFT4ABC123")
        self.assertEqual(stk.status, "paid")
        self.assertEqual(Ledger.query.one().outstanding_balance, Decimal("0.50"))

    def test_repeated_callbacks_record_one_payment(self):
        self._connect()
        self._prompt("500")
        stk = StkPushRequest.query.one()

        self._callback(stk)
        self._callback(stk)

        self.assertEqual(Payment.query.count(), 1)

    def test_cancelled_prompt_records_no_payment(self):
        self._connect()
        self._prompt("500")
        stk = StkPushRequest.query.one()

        self._callback(stk, result_code=1032)

        self.assertEqual(stk.status, "cancelled")
        self.assertEqual(stk.result_desc, "The customer cancelled the prompt.")
        self.assertEqual(Payment.query.count(), 0)

    def test_callback_with_wrong_token_or_checkout_id_is_ignored(self):
        self._connect()
        self._prompt("500")
        stk = StkPushRequest.query.one()

        self.client.post("/mpesa/callback/not-the-token", json={"Body": {"stkCallback": {
            "CheckoutRequestID": stk.checkout_request_id, "ResultCode": 0}}})
        self.client.post(f"/mpesa/callback/{stk.callback_token}", json={"Body": {"stkCallback": {
            "CheckoutRequestID": "ws_CO_forged", "ResultCode": 0}}})

        self.assertEqual(stk.status, "pending")
        self.assertEqual(Payment.query.count(), 0)

    def test_check_status_confirms_a_payment_without_a_callback(self):
        self._connect()
        self._prompt("500")
        stk = StkPushRequest.query.one()
        self.requests.post.return_value = fake_response(
            200, {"ResponseCode": "0", "ResultCode": "0", "ResultDesc": "The service request is processed successfully."}
        )

        self.client.post(f"/mpesa/requests/{stk.stk_request_id}/check")

        self.assertEqual(stk.status, "paid")
        self.assertEqual(Payment.query.one().amount_paid, Decimal("500.00"))

    def test_check_status_while_the_customer_is_still_deciding(self):
        self._connect()
        self._prompt("500")
        stk = StkPushRequest.query.one()
        self.requests.post.return_value = fake_response(
            500, {"errorCode": "500.001.1001", "errorMessage": "The transaction is being processed"}
        )

        self.client.post(f"/mpesa/requests/{stk.stk_request_id}/check")
        page = self.client.get(f"/customers/{self.customer.customer_id}").get_data(as_text=True)

        self.assertEqual(stk.status, "pending")
        self.assertIn("has not answered the prompt yet", page)

    def test_customer_page_shows_the_prompt_card_and_history(self):
        self._connect()
        self._prompt("500")
        self._callback(StkPushRequest.query.one())

        page = self.client.get(f"/customers/{self.customer.customer_id}").get_data(as_text=True)

        self.assertIn("Request payment by M-Pesa", page)
        self.assertIn("Paybill 174379", page)
        self.assertIn("Ref QFT4ABC123", page)

    def test_customer_form_records_consent(self):
        self.client.post(
            f"/customers/{self.customer.customer_id}/edit",
            data={"full_name": "Peter Kamau", "phone_number": "+254712345678", "preferred_channel": "sms"},
        )
        self.assertFalse(db.session.get(Customer, self.customer.customer_id).accepts_stk_prompts)

        self.client.post(
            f"/customers/{self.customer.customer_id}/edit",
            data={"full_name": "Peter Kamau", "phone_number": "+254712345678",
                  "preferred_channel": "sms", "accepts_stk_prompts": "y"},
        )
        self.assertTrue(db.session.get(Customer, self.customer.customer_id).accepts_stk_prompts)


if __name__ == "__main__":
    unittest.main()
