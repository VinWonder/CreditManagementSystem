"""Subscription access, payments and the app's own M-Pesa account."""

import math
from datetime import datetime

from flask import current_app

from app.billing.plans import PLANS, RENEWAL_NOTICE_DAYS
from app.mpesa.daraja import CANCELLED_CODES, DarajaClient

# Daraja result codes, worded for the shop owner paying their own subscription.
RESULT_MESSAGES = {
    "0": "Paid",
    "1": "Your M-Pesa balance was too low for this payment.",
    "1032": "The prompt was cancelled.",
    "1037": "The phone did not answer the prompt in time.",
    "2001": "The M-Pesa PIN was wrong.",
}


def shop_of(user):
    """The retailer whose subscription covers this user (a shopkeeper's employer)."""
    return user if user.is_admin else user.retailer


def days_left(moment, now=None):
    """Whole days, rounded up, until `moment` (0 once it has passed)."""
    if moment is None:
        return 0
    seconds = (moment - (now or datetime.utcnow())).total_seconds()
    return max(math.ceil(seconds / 86400), 0)


def renewal_notice(retailer):
    """What the banner should say about the trial or plan running out, or None."""
    status = retailer.subscription_status
    if status == "trial":
        left = days_left(retailer.trial_ends_at)
        return {"status": status, "days": left}
    if status == "active":
        left = days_left(retailer.paid_until)
        if left <= RENEWAL_NOTICE_DAYS:
            return {"status": status, "days": left}
    return None


def billing_client():
    """A Daraja client for the app's own Paybill/Till, or None when it is not set up."""
    config = current_app.config
    account_type = config.get("BILLING_MPESA_ACCOUNT_TYPE", "paybill")
    required = [
        config.get("BILLING_MPESA_SHORTCODE"),
        config.get("BILLING_MPESA_CONSUMER_KEY"),
        config.get("BILLING_MPESA_CONSUMER_SECRET"),
        config.get("BILLING_MPESA_PASSKEY"),
    ]
    if account_type == "till":
        required.append(config.get("BILLING_MPESA_TILL_NUMBER"))
    if not all(required):
        return None
    return DarajaClient(
        environment=config.get("BILLING_MPESA_ENVIRONMENT", "production"),
        account_type=account_type,
        shortcode=config["BILLING_MPESA_SHORTCODE"],
        till_number=config.get("BILLING_MPESA_TILL_NUMBER") or None,
        consumer_key=config["BILLING_MPESA_CONSUMER_KEY"],
        consumer_secret=config["BILLING_MPESA_CONSUMER_SECRET"],
        passkey=config["BILLING_MPESA_PASSKEY"],
    )


def grant_plan(retailer, payment, now=None):
    """Add the payment's plan time to the shop.

    Paid time starts when the current trial or plan ends, so paying early
    never loses days.
    """
    now = now or datetime.utcnow()
    current_end = retailer.access_ends_at
    start = current_end if current_end and current_end > now else now
    end = start + PLANS[payment.plan].period
    retailer.paid_until = end
    payment.period_start, payment.period_end = start, end


def paybill_details():
    """The bank Paybill shops pay into by hand, or None when there is none."""
    config = current_app.config
    number, account = config.get("BILLING_PAYBILL_NUMBER"), config.get("BILLING_PAYBILL_ACCOUNT")
    if not (number and account):
        return None
    return {"number": number, "account": account, "name": config.get("BILLING_PAYBILL_NAME", "")}


def confirm_paybill_claim(payment):
    """The money for a submitted M-Pesa code arrived: start the shop's plan."""
    payment.status = "paid"
    payment.completed_at = datetime.utcnow()
    grant_plan(payment.retailer, payment)


def finish_subscription_payment(payment, result_code, result_desc, receipt=None):
    """Record how a subscription prompt ended; a paid prompt extends the shop's access.

    Safe to call twice: only a pending payment changes.
    """
    if payment.status != "pending":
        return False
    result_code = str(result_code)
    payment.result_code = result_code
    payment.result_desc = (RESULT_MESSAGES.get(result_code) or result_desc or "")[:255]
    payment.completed_at = datetime.utcnow()
    if result_code != "0":
        payment.status = "cancelled" if result_code in CANCELLED_CODES else "failed"
        return True
    payment.status = "paid"
    payment.mpesa_receipt = receipt
    grant_plan(payment.retailer, payment)
    return True
