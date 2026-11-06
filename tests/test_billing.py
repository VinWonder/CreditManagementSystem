import unittest
from datetime import datetime, timedelta
from unittest.mock import MagicMock, patch

from flask import g
from flask_login import login_user

from app import create_app
from app.billing.cli import billing_cli
from app.billing.plans import PLANS, TRIAL_DAYS
from app.extensions import db
from app.models import C2BReceipt, Retailer, Shopkeeper, SubscriptionPayment
from app.mpesa import daraja


class BillingConfig:
    APP_ENV = "testing"
    SECRET_KEY = "test-secret"
    SQLALCHEMY_DATABASE_URI = "sqlite://"
    SQLALCHEMY_TRACK_MODIFICATIONS = False
    SQLALCHEMY_ENGINE_OPTIONS = {}
    AUTO_CREATE_DB = False
    WTF_CSRF_ENABLED = False
    DEFAULT_PHONE_REGION = "KE"
    MPESA_REQUEST_TIMEOUT_SECONDS = 5
    PUBLIC_BASE_URL = "https://duka.example.com"
    BILLING_MPESA_ENVIRONMENT = "sandbox"
    BILLING_MPESA_ACCOUNT_TYPE = "paybill"
    BILLING_MPESA_SHORTCODE = "174379"
    BILLING_MPESA_CONSUMER_KEY = "key"
    BILLING_MPESA_CONSUMER_SECRET = "secret"
    BILLING_MPESA_PASSKEY = "passkey"
    BILLING_PAY_INSTRUCTIONS = "Pay to Till 555555."


class ManualBillingConfig(BillingConfig):
    BILLING_MPESA_SHORTCODE = ""


class PaybillBillingConfig(ManualBillingConfig):
    BILLING_PAYBILL_NUMBER = "542542"
    BILLING_PAYBILL_ACCOUNT = "01234567890123"
    BILLING_PAYBILL_NAME = "I&M Bank"


def fake_response(status, body):
    response = MagicMock(status_code=status)
    response.json.return_value = body
    return response


TOKEN = fake_response(200, {"access_token": "test-token", "expires_in": "3599"})
STK_ACCEPTED = fake_response(
    200,
    {
        "MerchantRequestID": "29115-1",
        "CheckoutRequestID": "ws_CO_SUB_1",
        "ResponseCode": "0",
        "ResponseDescription": "Success. Request accepted for processing",
    },
)


class BillingTestCase(unittest.TestCase):
    config = BillingConfig

    def setUp(self):
        self.app = create_app(self.config)
        self.app_context = self.app.app_context()
        self.app_context.push()
        db.create_all()
        self.client = self.app.test_client()
        self.retailer = Retailer(
            business_name="Mama Njeri Shop",
            owner_name="Njeri",
            phone_number="+254700000041",
            password_hash="x",
        )
        db.session.add(self.retailer)
        db.session.flush()
        self.shopkeeper = Shopkeeper(
            retailer_id=self.retailer.retailer_id,
            full_name="Clerk",
            phone_number="+254700000042",
            password_hash="x",
        )
        db.session.add(self.shopkeeper)
        db.session.commit()
        self.user = self.retailer

        def authenticate():
            g.pop("_login_user", None)
            if self.user is not None:
                login_user(self.user)

        # Sign in before the subscription check runs.
        self.app.before_request_funcs.setdefault(None, []).insert(0, authenticate)

        daraja._token_cache.clear()
        self.addCleanup(daraja._token_cache.clear)
        patcher = patch("app.mpesa.daraja.requests")
        self.requests = patcher.start()
        self.addCleanup(patcher.stop)
        self.requests.RequestException = Exception
        self.requests.get.return_value = TOKEN
        self.requests.post.return_value = STK_ACCEPTED

    def tearDown(self):
        db.session.remove()
        db.drop_all()
        self.app_context.pop()

    def expire(self, retailer=None):
        retailer = retailer or self.retailer
        retailer.trial_ends_at = datetime.utcnow() - timedelta(minutes=1)
        retailer.paid_until = None
        db.session.commit()


class TrialAndAccessTests(BillingTestCase):
    def test_new_shop_gets_a_one_week_trial(self):
        left = self.retailer.trial_ends_at - datetime.utcnow()
        self.assertEqual(TRIAL_DAYS, 7)
        self.assertGreater(left, timedelta(days=6, hours=23))
        self.assertLessEqual(left, timedelta(days=7))
        self.assertEqual(self.retailer.subscription_status, "trial")
        response = self.client.get("/dashboard")
        self.assertEqual(response.status_code, 200)
        self.assertIn(b"Free trial: 7 days left", response.data)

    def test_expired_shop_is_sent_to_the_plans_page(self):
        self.expire()
        response = self.client.get("/dashboard")
        self.assertEqual(response.status_code, 302)
        self.assertTrue(response.location.endswith("/billing/"))
        page = self.client.get("/billing/")
        self.assertEqual(page.status_code, 200)
        self.assertIn(b"Your subscription has ended", page.data)
        # Account settings and signing out still work.
        self.assertEqual(self.client.get("/account/profile").status_code, 200)

    def test_expired_shop_keeps_offline_sales_queued(self):
        self.expire()
        response = self.client.post("/transactions/api/sync", json={"transactions": [{"client_id": "a"}]})
        self.assertEqual(response.status_code, 402)
        self.assertEqual(response.get_json()["results"], [])

    def test_free_forever_shop_is_never_blocked_or_asked_to_pay(self):
        self.retailer.billing_exempt = True
        self.expire()
        self.assertEqual(self.retailer.subscription_status, "free")
        response = self.client.get("/dashboard")
        self.assertEqual(response.status_code, 200)
        self.assertNotIn(b"Choose a plan", response.data)
        page = self.client.get("/billing/")
        self.assertIn(b"Free forever", page.data)
        self.assertNotIn(b"Pay with M-Pesa", page.data)
        self.client.post("/billing/subscribe", data={"plan": "monthly", "phone_number": "0712345678"})
        self.requests.post.assert_not_called()

    def test_shopkeeper_of_an_expired_shop_is_told_to_ask_the_owner(self):
        self.expire()
        self.user = self.shopkeeper
        self.assertEqual(self.client.get("/customers/").status_code, 302)
        page = self.client.get("/billing/")
        self.assertIn(b"Only the shop owner, Njeri, can pay", page.data)
        response = self.client.post("/billing/subscribe", data={"plan": "monthly", "phone_number": "0712345678"})
        self.assertEqual(response.status_code, 403)

    def test_banner_warns_only_near_the_end_of_a_paid_plan(self):
        self.retailer.trial_ends_at = datetime.utcnow() - timedelta(days=1)
        self.retailer.paid_until = datetime.utcnow() + timedelta(days=20)
        db.session.commit()
        self.assertNotIn(b"Choose a plan", self.client.get("/dashboard").data)
        self.retailer.paid_until = datetime.utcnow() + timedelta(days=2, hours=1)
        db.session.commit()
        self.assertIn(b"Your plan ends in 3 days", self.client.get("/dashboard").data)


class PlanTests(unittest.TestCase):
    def test_longer_plans_save_against_paying_monthly(self):
        self.assertEqual(PLANS["monthly"].saving, 0)
        self.assertEqual(PLANS["weekly"].saving, 0)
        self.assertGreater(PLANS["quarterly"].saving, 0)
        self.assertGreater(PLANS["yearly"].saving, PLANS["quarterly"].saving)
        self.assertLess(PLANS["yearly"].per_day, PLANS["monthly"].per_day)


class MpesaSubscriptionTests(BillingTestCase):
    def _subscribe(self, plan="monthly", phone="0712345678"):
        return self.client.post("/billing/subscribe", data={"plan": plan, "phone_number": phone})

    def _callback(self, payment, result_code=0, receipt="SUB4ABC123"):
        body = {
            "MerchantRequestID": payment.merchant_request_id,
            "CheckoutRequestID": payment.checkout_request_id,
            "ResultCode": result_code,
            "ResultDesc": "Processed",
        }
        if result_code == 0:
            body["CallbackMetadata"] = {
                "Item": [
                    {"Name": "Amount", "Value": payment.amount},
                    {"Name": "MpesaReceiptNumber", "Value": receipt},
                ]
            }
        self.user = None
        return self.client.post(
            f"/billing/callback/{payment.callback_token}", json={"Body": {"stkCallback": body}}
        )

    def test_prompt_charges_the_plan_price_to_the_apps_paybill(self):
        response = self._subscribe("yearly")
        payment = SubscriptionPayment.query.one()
        self.assertTrue(response.location.endswith(f"/billing/payments/{payment.subscription_payment_id}"))
        sent = self.requests.post.call_args.kwargs["json"]
        self.assertEqual(sent["Amount"], PLANS["yearly"].price)
        self.assertEqual(sent["BusinessShortCode"], "174379")
        self.assertEqual(sent["PartyA"], "254712345678")
        self.assertTrue(sent["CallBackURL"].startswith("https://duka.example.com/billing/callback/"))
        self.assertEqual(payment.status, "pending")
        self.assertEqual(payment.amount, PLANS["yearly"].price)

    def test_paying_during_the_trial_adds_time_after_the_trial(self):
        trial_end = self.retailer.trial_ends_at
        self._subscribe("monthly")
        payment = SubscriptionPayment.query.one()
        self._callback(payment)
        db.session.refresh(self.retailer)
        self.assertEqual(payment.status, "paid")
        self.assertEqual(payment.mpesa_receipt, "SUB4ABC123")
        self.assertEqual(payment.period_start, trial_end)
        self.assertEqual(self.retailer.paid_until, trial_end + timedelta(days=30))
        self.assertEqual(self.retailer.subscription_status, "active")

    def test_paying_after_expiry_restores_access_from_now(self):
        self.expire()
        self._subscribe("weekly")
        payment = SubscriptionPayment.query.one()
        self._callback(payment)
        db.session.refresh(self.retailer)
        left = self.retailer.paid_until - datetime.utcnow()
        self.assertGreater(left, timedelta(days=6, hours=23))
        self.user = self.retailer
        self.assertEqual(self.client.get("/dashboard").status_code, 200)

    def test_cancelled_prompt_adds_no_time_and_a_repeat_callback_changes_nothing(self):
        self._subscribe()
        payment = SubscriptionPayment.query.one()
        self._callback(payment, result_code=1032)
        self._callback(payment, result_code=0)
        db.session.refresh(self.retailer)
        self.assertEqual(payment.status, "cancelled")
        self.assertIsNone(self.retailer.paid_until)

    def test_waiting_page_asks_safaricom_when_no_callback_arrives(self):
        self._subscribe()
        payment = SubscriptionPayment.query.one()
        self.requests.post.return_value = fake_response(
            200, {"ResponseCode": "0", "ResultCode": "0", "ResultDesc": "Processed"}
        )
        data = self.client.get(f"/billing/payments/{payment.subscription_payment_id}/status").get_json()
        self.assertEqual(data["status"], "paid")
        self.assertIn("Duka Credit is yours until", data["message"])

    def test_second_prompt_is_held_back_while_one_is_waiting(self):
        self._subscribe()
        self._subscribe()
        self.assertEqual(SubscriptionPayment.query.count(), 1)

    def test_unknown_plan_and_non_safaricom_numbers_are_refused(self):
        self.assertEqual(self._subscribe(plan="lifetime").status_code, 400)
        self.assertEqual(self._subscribe(phone="+255712345678").status_code, 400)
        self.requests.post.assert_not_called()


class ManualPaymentTests(BillingTestCase):
    config = ManualBillingConfig

    def test_without_the_apps_paybill_the_page_shows_how_to_pay(self):
        page = self.client.get("/billing/")
        self.assertIn(b"Pay to Till 555555.", page.data)
        self.assertNotIn(b"Pay with M-Pesa</button>", page.data)

    def test_cli_records_a_payment_and_marks_shops_free(self):
        runner = self.app.test_cli_runner()
        self.expire()
        result = runner.invoke(billing_cli, ["activate", "mama njeri shop", "quarterly", "--receipt", "qx12"])
        self.assertEqual(result.exit_code, 0, result.output)
        db.session.refresh(self.retailer)
        payment = SubscriptionPayment.query.one()
        self.assertEqual(
            (payment.method, payment.amount, payment.mpesa_receipt),
            ("manual", PLANS["quarterly"].price, "QX12"),
        )
        self.assertEqual(self.retailer.subscription_status, "active")

        result = runner.invoke(billing_cli, ["free", "Mama Njeri Shop"])
        self.assertIn("FREE FOREVER", result.output)
        db.session.refresh(self.retailer)
        self.assertTrue(self.retailer.billing_exempt)

        result = runner.invoke(billing_cli, ["list"])
        self.assertIn("Mama Njeri Shop", result.output)


class PaybillClaimTests(BillingTestCase):
    config = PaybillBillingConfig

    def _claim(self, code="skl4 abc123", plan="monthly"):
        return self.client.post("/billing/claim", data={"plan": plan, "mpesa_code": code})

    def test_page_shows_the_bank_paybill_steps_with_the_plan_amount(self):
        page = self.client.get("/billing/?plan=yearly").data.decode()
        self.assertIn("542542", page)
        self.assertIn("01234567890123", page)
        self.assertIn("I&amp;M Bank", page)
        self.assertIn(f"KES <span data-plan-amount>{PLANS['yearly'].price:,}</span>", page)
        self.assertIn("Send code", page)

    def test_code_waits_for_confirmation_before_the_plan_starts(self):
        self.expire()
        self._claim()
        payment = SubscriptionPayment.query.one()
        self.assertEqual((payment.method, payment.status, payment.mpesa_receipt), ("paybill", "pending", "SKL4ABC123"))
        self.assertEqual(payment.amount, PLANS["monthly"].price)
        self.assertIn(b"Checking M-Pesa code", self.client.get("/billing/").data)
        self.assertEqual(self.client.get("/dashboard").status_code, 302)

        result = self.app.test_cli_runner().invoke(billing_cli, ["confirm", "skl4abc123"])
        self.assertEqual(result.exit_code, 0, result.output)
        db.session.refresh(self.retailer)
        self.assertEqual(self.retailer.subscription_status, "active")
        self.assertEqual(self.client.get("/dashboard").status_code, 200)

    def test_rejected_code_is_shown_and_adds_no_time(self):
        self._claim()
        runner = self.app.test_cli_runner()
        self.assertIn("SKL4ABC123", runner.invoke(billing_cli, ["pending"]).output)
        runner.invoke(billing_cli, ["reject", "SKL4ABC123"])
        db.session.refresh(self.retailer)
        self.assertIsNone(self.retailer.paid_until)
        self.assertIn(b"Not found on the bank statement", self.client.get("/billing/").data)

    def test_bad_or_repeated_codes_are_refused(self):
        self.assertEqual(self._claim(code="12345").status_code, 400)
        self._claim()
        self._claim(code="SKL4ABC123")
        self.assertEqual(SubscriptionPayment.query.count(), 1)

    def test_paybill_claim_never_appears_as_a_prompt(self):
        self._claim()
        payment = SubscriptionPayment.query.one()
        response = self.client.get(f"/billing/payments/{payment.subscription_payment_id}/status")
        self.assertEqual(response.status_code, 404)


class OwnPaybillConfig(BillingConfig):
    BILLING_C2B_TOKEN = "c2b-secret"


class AutomaticPaybillTests(BillingTestCase):
    """Pay Bill payments to the app's own Paybill confirm themselves."""

    config = OwnPaybillConfig

    def _confirmation(self, trans_id="SKL4AUTO01", amount="1000.00", account=None, token="c2b-secret"):
        self.user = None
        response = self.client.post(
            f"/billing/c2b/{token}/confirmation",
            json={
                "TransactionType": "Pay Bill",
                "TransID": trans_id,
                "TransTime": "20261106143000",
                "TransAmount": amount,
                "BusinessShortCode": "174379",
                "BillRefNumber": account if account is not None else self.retailer.billing_account,
                "FirstName": "NJERI",
            },
        )
        self.user = self.retailer
        self.assertEqual(response.get_json()["ResultCode"], 0)
        db.session.refresh(self.retailer)
        return response

    def test_page_shows_the_shops_own_account_number(self):
        page = self.client.get("/billing/").data.decode()
        self.assertIn(f"Account number: <strong>{self.retailer.billing_account}</strong>", page)
        self.assertIn("Business number: <strong>174379</strong>", page)

    def test_payment_with_the_shop_account_starts_the_plan_by_itself(self):
        self.expire()
        self._confirmation()
        payment = SubscriptionPayment.query.one()
        self.assertEqual((payment.method, payment.plan, payment.status), ("c2b", "monthly", "paid"))
        self.assertEqual(self.retailer.subscription_status, "active")
        self.assertEqual(self.client.get("/dashboard").status_code, 200)

    def test_shop_phone_number_works_as_the_account_and_amount_picks_the_plan(self):
        self._confirmation(amount="10000", account="0700 000 041")
        self.assertEqual(SubscriptionPayment.query.one().plan, "yearly")

    def test_repeated_confirmation_counts_once(self):
        self._confirmation()
        first_end = self.retailer.paid_until
        self._confirmation()
        self.assertEqual(SubscriptionPayment.query.count(), 1)
        self.assertEqual(self.retailer.paid_until, first_end)

    def test_wrong_token_is_ignored(self):
        self._confirmation(token="guess")
        self.assertEqual(C2BReceipt.query.count(), 0)
        self.assertIsNone(self.retailer.paid_until)

    def test_unknown_account_is_kept_until_the_shop_sends_its_code(self):
        self._confirmation(account="WRONG")
        receipt = C2BReceipt.query.one()
        self.assertIsNone(receipt.subscription_payment_id)
        self.assertIsNone(self.retailer.paid_until)
        self.assertIn("SKL4AUTO01", self.app.test_cli_runner().invoke(billing_cli, ["pending"]).output)

        self.client.post("/billing/claim", data={"plan": "monthly", "mpesa_code": "skl4auto01"})
        self.assertIn(b"Payment confirmed", self.client.get("/billing/").data)
        db.session.refresh(self.retailer)
        self.assertEqual(self.retailer.subscription_status, "active")
        self.assertIsNotNone(C2BReceipt.query.one().subscription_payment_id)

    def test_code_sent_first_is_confirmed_when_safaricom_reports_it(self):
        self.client.post("/billing/claim", data={"plan": "quarterly", "mpesa_code": "SKL4AUTO01"})
        self.assertIsNone(self.retailer.paid_until)
        self._confirmation(amount="2700", account="WRONG")
        claim = SubscriptionPayment.query.one()
        self.assertEqual((claim.status, claim.plan), ("paid", "quarterly"))

    def test_amount_below_the_cheapest_plan_starts_nothing(self):
        self._confirmation(amount="50")
        self.assertEqual(SubscriptionPayment.query.count(), 0)
        self.assertEqual(C2BReceipt.query.one().amount, 50)

    def test_register_command_sends_the_confirmation_address(self):
        result = self.app.test_cli_runner().invoke(billing_cli, ["register-c2b"])
        self.assertEqual(result.exit_code, 0, result.output)
        sent = self.requests.post.call_args.kwargs["json"]
        self.assertEqual(sent["ShortCode"], "174379")
        self.assertEqual(
            sent["ConfirmationURL"], "https://duka.example.com/billing/c2b/c2b-secret/confirmation"
        )


if __name__ == "__main__":
    unittest.main()
