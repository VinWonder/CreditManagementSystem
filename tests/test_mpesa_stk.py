import base64
import unittest
from datetime import datetime, timedelta
from decimal import Decimal
from unittest.mock import MagicMock, patch

from flask import g
from flask_login import login_user

from app import create_app
from app.extensions import db
from app.models import (
    CreditTransaction,
    Customer,
    Ledger,
    Payment,
    Retailer,
    Shopkeeper,
    StkPushRequest,
)
from app.mpesa.daraja import account_reference_for, missing_mpesa_settings, safaricom_msisdn


class MpesaConfig:
    APP_ENV = "testing"
    SECRET_KEY = "test-secret"
    SQLALCHEMY_DATABASE_URI = "sqlite://"
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    SQLALCHEMY_ENGINE_OPTIONS = {}
    AUTO_CREATE_DB = False
    WTF_CSRF_ENABLED = False
    DEFAULT_PHONE_REGION = "KE"
    MPESA_ENVIRONMENT = "sandbox"
    MPESA_ACCOUNT_TYPE = "paybill"
    MPESA_SHORTCODE = "174379"
    MPESA_TILL_NUMBER = ""
    MPESA_CONSUMER_KEY = "key"
    MPESA_CONSUMER_SECRET = "secret"
    MPESA_PASSKEY = "passkey"
    MPESA_REQUEST_TIMEOUT_SECONDS = 5
    PUBLIC_BASE_URL = "https://duka.example.com"


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
    },
)
STILL_WAITING = fake_response(
    500, {"errorCode": "500.001.1001", "errorMessage": "The transaction is being processed"}
)
QUERY_PAID = fake_response(
    200, {"ResponseCode": "0", "ResultCode": "0", "ResultDesc": "The service request is processed successfully."}
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
        self.assertEqual(account_reference_for("!!!"), "DUKA CREDIT")

    def test_missing_settings_are_named(self):
        config = {k: getattr(MpesaConfig, k) for k in dir(MpesaConfig) if k.startswith("MPESA_")}
        self.assertEqual(missing_mpesa_settings(config), [])
        self.assertEqual(
            missing_mpesa_settings(dict(config, MPESA_PASSKEY="", MPESA_ACCOUNT_TYPE="till")),
            ["MPESA_PASSKEY", "MPESA_TILL_NUMBER"],
        )


class StkPushPaymentTests(unittest.TestCase):
    config = MpesaConfig

    def setUp(self):
        self.app = create_app(self.config)
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
                    amount=Decimal("500.00"),
                    transaction_date=datetime(2026, 10, 1),
                ),
                Ledger(
                    retailer_id=self.retailer.retailer_id,
                    customer_id=self.customer.customer_id,
                    outstanding_balance=Decimal("500.00"),
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

    def _prompt(self, amount="500", phone="0712345678"):
        return self.client.post(
            "/payments/new",
            data={
                "customer_id": str(self.customer.customer_id),
                "amount_paid": amount,
                "payment_method": "mpesa_stk",
                "stk_phone": phone,
            },
        )

    def _callback(self, stk, result_code=0, receipt="QFT4ABC123", amount=500):
        callback = {
            "MerchantRequestID": stk.merchant_request_id,
            "CheckoutRequestID": stk.checkout_request_id,
            "ResultCode": result_code,
            "ResultDesc": "Processed",
        }
        if result_code == 0:
            callback["CallbackMetadata"] = {
                "Item": [
                    {"Name": "Amount", "Value": amount},
                    {"Name": "MpesaReceiptNumber", "Value": receipt},
                    {"Name": "PhoneNumber", "Value": 254712345678},
                ]
            }
        return self.client.post(
            f"/mpesa/callback/{stk.callback_token}", json={"Body": {"stkCallback": callback}}
        )

    # Sending from Log payment

    def test_payment_form_offers_the_prompt_when_m_pesa_is_set_up(self):
        html = self.client.get("/payments/new").get_data(as_text=True)

        self.assertIn('value="mpesa_stk"', html)
        self.assertIn("M-Pesa prompt (STK Push)", html)
        self.assertIn('"1": "+254712345678"', html)

    def test_prompt_is_sent_with_shop_name_amount_and_number(self):
        response = self._prompt("500", "0712 345 678")

        stk = StkPushRequest.query.one()
        self.assertTrue(response.headers["Location"].endswith(f"/mpesa/requests/{stk.stk_request_id}"))
        payload = self.requests.post.call_args.kwargs["json"]
        self.assertEqual(payload["TransactionType"], "CustomerPayBillOnline")
        self.assertEqual(payload["Amount"], 500)
        self.assertEqual(payload["PartyA"], "254712345678")
        self.assertEqual(payload["PartyB"], "174379")
        self.assertEqual(payload["AccountReference"], "MAMA NJERI S")
        self.assertTrue(payload["CallBackURL"].startswith("https://duka.example.com/mpesa/callback/"))
        password = base64.b64decode(payload["Password"]).decode()
        self.assertEqual(password, f"174379passkey{payload['Timestamp']}")
        self.assertEqual(Payment.query.count(), 0)
        page = self.client.get(response.headers["Location"]).get_data(as_text=True)
        self.assertIn("KES 500 from Peter Kamau", page)
        self.assertIn("Waiting for the customer", page)

    def test_cents_and_amounts_above_the_balance_are_refused(self):
        cents = self._prompt("100.50")
        too_much = self._prompt("501")

        self.assertIn(b"M-Pesa prompts take whole shillings", cents.data)
        self.assertIn(b"Payment cannot exceed the outstanding balance", too_much.data)
        self.requests.post.assert_not_called()

    def test_non_safaricom_number_is_refused(self):
        response = self._prompt(phone="+255712345678")

        self.assertIn(b"Enter the customer&#39;s Safaricom number", response.data)
        self.requests.post.assert_not_called()

    def test_a_second_prompt_waits_for_the_first(self):
        self._prompt("100")
        response = self._prompt("100")

        self.assertIn(b"A prompt was just sent to this customer", response.data)
        self.assertEqual(StkPushRequest.query.count(), 1)

    def test_safaricom_errors_are_shown_and_nothing_is_saved(self):
        self.requests.post.return_value = fake_response(
            400, {"errorCode": "400.002.02", "errorMessage": "Bad Request - Invalid PhoneNumber"}
        )

        response = self._prompt()

        self.assertIn(b"Invalid PhoneNumber", response.data)
        self.assertEqual(StkPushRequest.query.count(), 0)

    def test_wrong_keys_are_explained(self):
        self.requests.get.return_value = fake_response(400, {})

        response = self._prompt()

        self.assertIn(b"refused the consumer key and secret", response.data)

    # Results

    def test_paid_callback_records_an_approved_payment(self):
        self._prompt("500")
        stk = StkPushRequest.query.one()

        response = self._callback(stk)

        self.assertEqual(response.get_json(), {"ResultCode": 0, "ResultDesc": "Accepted"})
        payment = Payment.query.one()
        self.assertEqual(payment.amount_paid, Decimal("500.00"))
        self.assertEqual(payment.approval_status, "approved")
        self.assertEqual(payment.payment_method, "mpesa_stk")
        self.assertEqual(payment.payment_reference, "QFT4ABC123")
        self.assertEqual(Ledger.query.one().outstanding_balance, Decimal("0.00"))
        history = self.client.get("/payments/").get_data(as_text=True)
        self.assertIn("M-Pesa prompt (STK Push)", history)
        self.assertIn("Ref QFT4ABC123", history)

    def test_shopkeeper_prompts_are_confirmed_by_m_pesa_not_the_admin(self):
        self.user = self.shopkeeper
        self._prompt("200")
        stk = StkPushRequest.query.one()
        self.assertEqual(stk.requested_by_shopkeeper_id, self.shopkeeper.shopkeeper_id)

        self._callback(stk, amount=200)

        payment = Payment.query.one()
        self.assertEqual(payment.approval_status, "approved")
        self.assertEqual(payment.submitted_by_shopkeeper_id, self.shopkeeper.shopkeeper_id)

    def test_repeated_callbacks_record_one_payment(self):
        self._prompt("500")
        stk = StkPushRequest.query.one()

        self._callback(stk)
        self._callback(stk)

        self.assertEqual(Payment.query.count(), 1)

    def test_cancelled_prompt_records_no_payment(self):
        self._prompt("500")
        stk = StkPushRequest.query.one()

        self._callback(stk, result_code=1032)

        self.assertEqual(stk.status, "cancelled")
        self.assertEqual(Payment.query.count(), 0)
        status = self.client.get(f"/mpesa/requests/{stk.stk_request_id}/status").get_json()
        self.assertEqual(status["message"], "The customer cancelled the prompt.")

    def test_callback_with_wrong_token_or_checkout_id_is_ignored(self):
        self._prompt("500")
        stk = StkPushRequest.query.one()

        self.client.post("/mpesa/callback/not-the-token", json={"Body": {"stkCallback": {
            "CheckoutRequestID": stk.checkout_request_id, "ResultCode": 0}}})
        self.client.post(f"/mpesa/callback/{stk.callback_token}", json={"Body": {"stkCallback": {
            "CheckoutRequestID": "ws_CO_forged", "ResultCode": 0}}})

        self.assertEqual(stk.status, "pending")
        self.assertEqual(Payment.query.count(), 0)

    def test_waiting_page_asks_safaricom_until_the_customer_pays(self):
        self._prompt("500")
        stk = StkPushRequest.query.one()
        url = f"/mpesa/requests/{stk.stk_request_id}/status"

        self.requests.post.return_value = STILL_WAITING
        self.assertEqual(self.client.get(url).get_json()["status"], "pending")

        self.requests.post.return_value = QUERY_PAID
        self.assertEqual(self.client.get(url).get_json()["status"], "paid")
        self.assertEqual(Payment.query.one().amount_paid, Decimal("500.00"))

    def test_unanswered_prompts_expire(self):
        self._prompt("500")
        stk = StkPushRequest.query.one()
        stk.created_at = datetime.utcnow() - timedelta(minutes=6)
        db.session.commit()

        status = self.client.get(f"/mpesa/requests/{stk.stk_request_id}/status").get_json()

        self.assertEqual(status["status"], "cancelled")
        self.assertEqual(Payment.query.count(), 0)

    def test_other_shops_cannot_see_a_prompt(self):
        self._prompt("500")
        stk = StkPushRequest.query.one()
        other = Retailer(business_name="Other", owner_name="O", phone_number="+254700000039", password_hash="x")
        db.session.add(other)
        db.session.commit()
        self.user = other

        self.assertEqual(self.client.get(f"/mpesa/requests/{stk.stk_request_id}").status_code, 404)
        self.assertEqual(self.client.get(f"/mpesa/requests/{stk.stk_request_id}/status").status_code, 404)

    # Removed pieces

    def test_no_settings_page_or_consent_tick_box(self):
        self.assertEqual(self.client.get("/mpesa/settings").status_code, 404)
        dashboard = self.client.get("/dashboard").get_data(as_text=True)
        self.assertNotIn("/mpesa/settings", dashboard)
        form = self.client.get(f"/customers/{self.customer.customer_id}/edit").get_data(as_text=True)
        self.assertNotIn("M-Pesa payment prompts", form)


class StkPushNotConfiguredTests(StkPushPaymentTests):
    """Without MPESA_* settings the option is hidden and cannot be forced."""

    class config(MpesaConfig):
        MPESA_CONSUMER_KEY = ""

    def test_payment_form_offers_the_prompt_when_m_pesa_is_set_up(self):
        html = self.client.get("/payments/new").get_data(as_text=True)
        self.assertNotIn('value="mpesa_stk"', html)

    def test_prompt_cannot_be_forced(self):
        response = self._prompt()

        self.assertIn(b"Not a valid choice", response.data)
        self.requests.post.assert_not_called()


# Only the two overrides above apply to the unconfigured case.
for _name in list(vars(StkPushPaymentTests)):
    if _name.startswith("test_") and _name not in vars(StkPushNotConfiguredTests):
        setattr(StkPushNotConfiguredTests, _name, None)


if __name__ == "__main__":
    unittest.main()
