import secrets
from datetime import datetime, timedelta
from decimal import ROUND_CEILING, Decimal

from flask import Blueprint, current_app, flash, jsonify, redirect, render_template, request, url_for
from flask_login import current_user, login_required

from app.extensions import db
from app.models import Customer, Ledger, MpesaSettings, Payment, StkPushRequest
from app.mpesa.daraja import (
    CANCELLED_CODES,
    RESULT_MESSAGES,
    DarajaClient,
    DarajaError,
    account_reference_for,
    safaricom_msisdn,
)
from app.mpesa.forms import MpesaSettingsForm, StkActionForm, StkPushForm
from app.permissions import admin_required
from app.reminders.credentials import MessagingCredentialError, encrypt_secret

mpesa_bp = Blueprint("mpesa", __name__, url_prefix="/mpesa")

# Safaricom keeps a prompt open for about a minute; don't stack prompts on one phone.
PROMPT_COOLDOWN = timedelta(minutes=2)


def callback_base_url():
    """The public HTTPS address Safaricom can reach, or None when not configured."""
    base = current_app.config.get("PUBLIC_BASE_URL") or ""
    return base if base.startswith("https://") else None


@mpesa_bp.route("/settings", methods=["GET", "POST"])
@login_required
@admin_required
def settings():
    saved = MpesaSettings.query.filter_by(retailer_id=current_user.retailer_id).first()
    form = MpesaSettingsForm(obj=saved, has_saved_secrets=saved is not None)
    if form.validate_on_submit():
        try:
            new_secrets = {
                name: encrypt_secret(getattr(form, name).data.strip())
                for name in ("consumer_key", "consumer_secret", "passkey")
                if (getattr(form, name).data or "").strip()
            }
        except MessagingCredentialError as exc:
            flash(f"{exc} Set MESSAGING_CREDENTIALS_ENCRYPTION_KEY in .env.", "danger")
            return render_template("mpesa/settings.html", form=form, saved=saved,
                                   callback_base=callback_base_url(),
                                   account_reference=account_reference_for(current_user.business_name))

        if saved is None:
            saved = MpesaSettings(retailer_id=current_user.retailer_id)
            db.session.add(saved)
        saved.environment = form.environment.data
        saved.account_type = form.account_type.data
        saved.business_shortcode = form.business_shortcode.data.strip()
        saved.till_number = (form.till_number.data or "").strip() or None
        if saved.account_type != "till":
            saved.till_number = None
        for name, value in new_secrets.items():
            setattr(saved, f"{name}_encrypted", value)
        saved.updated_at = datetime.utcnow()
        db.session.commit()
        flash("M-Pesa settings saved. You can now send payment prompts.", "success")
        return redirect(url_for("mpesa.settings"))

    return render_template(
        "mpesa/settings.html",
        form=form,
        saved=saved,
        callback_base=callback_base_url(),
        account_reference=account_reference_for(current_user.business_name),
    )


def finish_stk_request(stk, result_code, result_desc, receipt=None, paid_amount=None):
    """Record how a prompt ended; a paid prompt becomes an approved payment.

    Safe to call twice (callback and status check): only a pending prompt changes.
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

    settings = MpesaSettings.query.filter_by(retailer_id=stk.retailer_id).first()
    till = settings is not None and settings.account_type == "till"
    payment = Payment(
        retailer_id=stk.retailer_id,
        customer_id=stk.customer_id,
        amount_paid=Decimal(str(paid_amount if paid_amount is not None else stk.amount)),
        payment_date=now,
        payment_method="mpesa_till" if till else "mpesa_paybill",
        payment_reference=receipt,
        approval_status="approved",
        reviewed_by_retailer_id=stk.retailer_id,
        reviewed_at=now,
        review_note="Paid through an M-Pesa prompt",
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


@mpesa_bp.route("/customers/<int:customer_id>/prompt", methods=["POST"])
@login_required
@admin_required
def send_prompt(customer_id):
    back = redirect(url_for("customers.view_customer", customer_id=customer_id))
    customer = Customer.query.filter_by(
        customer_id=customer_id, retailer_id=current_user.retailer_id
    ).first_or_404()
    form = StkPushForm()
    if not form.validate_on_submit():
        for errors in form.errors.values():
            for error in errors:
                flash(error, "danger")
        return back

    settings = MpesaSettings.query.filter_by(retailer_id=current_user.retailer_id).first()
    if settings is None:
        flash("Connect your Paybill or Till on the M-Pesa page first.", "warning")
        return redirect(url_for("mpesa.settings"))
    if not customer.accepts_stk_prompts:
        flash(
            "This customer has not agreed to M-Pesa prompts. "
            "Edit the customer to record their consent.",
            "warning",
        )
        return back

    ledger = customer.ledger
    if ledger:
        ledger.recalculate()
    balance = Decimal(ledger.outstanding_balance) if ledger else Decimal("0")
    if balance <= 0:
        flash("This customer has nothing to pay.", "info")
        return back
    # M-Pesa takes whole shillings, so a balance with cents rounds up.
    max_amount = int(balance.to_integral_value(rounding=ROUND_CEILING))
    if form.amount.data > max_amount:
        flash(f"The prompt cannot be more than the KES {max_amount:,} owed.", "danger")
        return back

    phone = safaricom_msisdn(form.phone_number.data)
    if not phone:
        flash("Enter a Safaricom number such as 0712 345 678.", "danger")
        return back

    recent = StkPushRequest.query.filter(
        StkPushRequest.customer_id == customer.customer_id,
        StkPushRequest.status == "pending",
        StkPushRequest.created_at > datetime.utcnow() - PROMPT_COOLDOWN,
    ).first()
    if recent:
        flash(
            "A prompt was just sent to this customer. "
            "Wait for them to answer, then check its status.",
            "warning",
        )
        return back

    token = secrets.token_urlsafe(32)
    callback_path = url_for("mpesa.callback", token=token)
    # Daraja needs an https URL. Without a public address this local one is
    # unreachable, and 'Check status' confirms the payment instead.
    callback_url = (callback_base_url() or f"https://{request.host}") + callback_path
    reference = account_reference_for(current_user.business_name)
    try:
        response = DarajaClient(settings).stk_push(
            phone, form.amount.data, reference, reference, callback_url
        )
    except (DarajaError, MessagingCredentialError) as exc:
        flash(str(exc), "danger")
        return back

    db.session.add(
        StkPushRequest(
            retailer_id=current_user.retailer_id,
            customer_id=customer.customer_id,
            phone_number=phone,
            amount=form.amount.data,
            account_reference=reference,
            callback_token=token,
            merchant_request_id=response.get("MerchantRequestID"),
            checkout_request_id=response.get("CheckoutRequestID"),
        )
    )
    db.session.commit()
    flash(
        f"M-Pesa prompt sent to {phone} for KES {form.amount.data:,}. "
        f"The customer enters their PIN to pay {reference}.",
        "success",
    )
    return back


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


@mpesa_bp.route("/requests/<int:stk_request_id>/check", methods=["POST"])
@login_required
@admin_required
def check_status(stk_request_id):
    stk = StkPushRequest.query.filter_by(
        stk_request_id=stk_request_id, retailer_id=current_user.retailer_id
    ).first_or_404()
    back = redirect(url_for("customers.view_customer", customer_id=stk.customer_id))
    if not StkActionForm().validate_on_submit():
        flash("The request could not be verified. Please try again.", "danger")
        return back
    if stk.status != "pending":
        flash(f"This prompt is already {stk.status}.", "info")
        return back

    settings = MpesaSettings.query.filter_by(retailer_id=current_user.retailer_id).first()
    if settings is None:
        flash("Connect your Paybill or Till on the M-Pesa page first.", "warning")
        return redirect(url_for("mpesa.settings"))
    try:
        result_code, result_desc = DarajaClient(settings).stk_query(stk.checkout_request_id)
    except (DarajaError, MessagingCredentialError) as exc:
        flash(str(exc), "danger")
        return back
    if result_code is None:
        flash(result_desc, "info")
        return back

    finish_stk_request(stk, result_code, result_desc)
    db.session.commit()
    if stk.status == "paid":
        flash(f"Payment of KES {stk.amount:,} confirmed and recorded.", "success")
    else:
        flash(f"The prompt was not paid: {stk.result_desc}", "warning")
    return back
