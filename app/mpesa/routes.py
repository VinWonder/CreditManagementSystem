import secrets
from datetime import datetime, timedelta
from decimal import Decimal

from flask import Blueprint, current_app, jsonify, render_template, request, url_for
from flask_login import current_user, login_required

from app.extensions import db
from app.models import Ledger, Payment, StkPushRequest
from app.mpesa.daraja import (
    CANCELLED_CODES,
    RESULT_MESSAGES,
    DarajaClient,
    DarajaError,
    account_reference_for,
)

mpesa_bp = Blueprint("mpesa", __name__, url_prefix="/mpesa")

# Safaricom keeps a prompt open for about a minute; don't stack prompts on one phone.
PROMPT_COOLDOWN = timedelta(minutes=2)
# After this, a prompt nobody answered is closed so it stops being checked.
PROMPT_EXPIRY = timedelta(minutes=5)


def callback_base_url():
    """The public HTTPS address Safaricom can reach, or None when not configured."""
    base = current_app.config.get("PUBLIC_BASE_URL") or ""
    return base if base.startswith("https://") else None


def recent_pending_prompt(customer_id):
    return StkPushRequest.query.filter(
        StkPushRequest.customer_id == customer_id,
        StkPushRequest.status == "pending",
        StkPushRequest.created_at > datetime.utcnow() - PROMPT_COOLDOWN,
    ).first()


def send_stk_prompt(customer, amount, phone):
    """Ask Safaricom to prompt the customer's phone. Raises DarajaError with a showable reason."""
    token = secrets.token_urlsafe(32)
    # Daraja needs an https URL. Without a public address this local one is
    # unreachable, and the waiting page asks Safaricom for the result instead.
    callback_url = (callback_base_url() or f"https://{request.host}") + url_for(
        "mpesa.callback", token=token
    )
    reference = account_reference_for(current_user.business_name)
    response = DarajaClient().stk_push(phone, amount, reference, reference, callback_url)
    stk = StkPushRequest(
        retailer_id=current_user.retailer_id,
        customer_id=customer.customer_id,
        requested_by_shopkeeper_id=None if current_user.is_admin else current_user.shopkeeper_id,
        phone_number=phone,
        amount=amount,
        account_reference=reference,
        callback_token=token,
        merchant_request_id=response.get("MerchantRequestID"),
        checkout_request_id=response.get("CheckoutRequestID"),
    )
    db.session.add(stk)
    db.session.commit()
    return stk


def finish_stk_request(stk, result_code, result_desc, receipt=None, paid_amount=None):
    """Record how a prompt ended; a paid prompt becomes an approved payment.

    Safaricom's confirmation is the proof of payment, so no admin approval is
    needed even when a shopkeeper sent the prompt. Safe to call twice: only a
    pending prompt changes.
    """
    if stk.status != "pending":
        return False
    now = datetime.utcnow()
    result_code = str(result_code)
    stk.result_code = result_code
    stk.result_desc = (RESULT_MESSAGES.get(result_code) or result_desc or "")[:255]
    stk.completed_at = now
    if result_code != "0":
        stk.status = "cancelled" if result_code in CANCELLED_CODES else "failed"
        return True

    payment = Payment(
        retailer_id=stk.retailer_id,
        customer_id=stk.customer_id,
        amount_paid=Decimal(str(paid_amount if paid_amount is not None else stk.amount)),
        payment_date=now,
        payment_method="mpesa_stk",
        payment_reference=receipt,
        approval_status="approved",
        submitted_by_shopkeeper_id=stk.requested_by_shopkeeper_id,
        reviewed_by_retailer_id=stk.retailer_id,
        reviewed_at=now,
        review_note="Confirmed by M-Pesa",
    )
    db.session.add(payment)
    db.session.flush()
    stk.payment_id = payment.payment_id
    stk.mpesa_receipt = receipt
    stk.status = "paid"

    ledger = Ledger.query.filter_by(customer_id=stk.customer_id).first()
    if ledger is None:
        ledger = Ledger(retailer_id=stk.retailer_id, customer_id=stk.customer_id)
        db.session.add(ledger)
        db.session.flush()
    ledger.recalculate()
    return True


def _owned_request_or_404(stk_request_id):
    return StkPushRequest.query.filter_by(
        stk_request_id=stk_request_id, retailer_id=current_user.retailer_id
    ).first_or_404()


def _status_payload(stk, message=None):
    messages = {
        "pending": "Waiting for the customer to enter their M-Pesa PIN.",
        "paid": f"Paid. KES {stk.amount:,} recorded as an approved payment.",
        "cancelled": stk.result_desc or "The customer did not pay.",
        "failed": stk.result_desc or "The payment did not go through.",
    }
    return {
        "status": stk.status,
        "message": message or messages[stk.status],
        "receipt": stk.mpesa_receipt,
    }


@mpesa_bp.route("/requests/<int:stk_request_id>")
@login_required
def prompt_status(stk_request_id):
    stk = _owned_request_or_404(stk_request_id)
    return render_template("mpesa/prompt_status.html", stk=stk, initial=_status_payload(stk))


@mpesa_bp.route("/requests/<int:stk_request_id>/status")
@login_required
def prompt_status_json(stk_request_id):
    """Polled by the waiting page; asks Safaricom when the callback has not arrived."""
    stk = _owned_request_or_404(stk_request_id)
    if stk.status != "pending":
        return jsonify(_status_payload(stk))

    if datetime.utcnow() - stk.created_at > PROMPT_EXPIRY:
        finish_stk_request(stk, "1037", "No answer from the customer.")
        db.session.commit()
        return jsonify(_status_payload(stk))

    try:
        result_code, result_desc = DarajaClient().stk_query(stk.checkout_request_id)
    except DarajaError as exc:
        return jsonify(_status_payload(stk, message=str(exc)))
    if result_code is not None:
        finish_stk_request(stk, result_code, result_desc)
        db.session.commit()
    return jsonify(_status_payload(stk))


@mpesa_bp.route("/callback/<token>", methods=["POST"])
def callback(token):
    """Safaricom reports the prompt result here. Always acknowledge so it does not retry."""
    accepted = jsonify(ResultCode=0, ResultDesc="Accepted")
    stk = StkPushRequest.query.filter_by(callback_token=token).first()
    body = (request.get_json(silent=True) or {}).get("Body", {}).get("stkCallback", {})
    if stk is None or not body or body.get("CheckoutRequestID") != stk.checkout_request_id:
        current_app.logger.warning("Ignored an M-Pesa callback that matched no prompt.")
        return accepted

    items = {
        item.get("Name"): item.get("Value")
        for item in body.get("CallbackMetadata", {}).get("Item", [])
    }
    finish_stk_request(
        stk,
        body.get("ResultCode"),
        body.get("ResultDesc"),
        receipt=items.get("MpesaReceiptNumber"),
        paid_amount=items.get("Amount"),
    )
    db.session.commit()
    return accepted
