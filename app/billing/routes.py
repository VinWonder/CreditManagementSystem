import secrets
from datetime import datetime, timedelta

from flask import (
    Blueprint,
    current_app,
    flash,
    jsonify,
    redirect,
    render_template,
    request,
    url_for,
)
from flask_login import current_user, login_required

from app.billing.forms import PaybillClaimForm, SubscribeForm
from app.billing.plans import DEFAULT_PLAN, PLANS, TRIAL_DAYS
from app.billing.service import (
    billing_client,
    days_left,
    finish_subscription_payment,
    paybill_details,
    shop_of,
)
from app.extensions import db
from app.models import SubscriptionPayment
from app.mpesa.daraja import DarajaError, account_reference_for, safaricom_msisdn
from app.mpesa.routes import PROMPT_COOLDOWN, PROMPT_EXPIRY, UNREACHABLE_CALLBACK_BASE, callback_base_url
from app.permissions import admin_required

billing_bp = Blueprint("billing", __name__, url_prefix="/billing")

# Pages a shop whose trial or plan has ended can still open: signing in and
# out, its own account settings, paying, and the public pages.
OPEN_BLUEPRINTS = {"auth", "account", "billing"}
OPEN_ENDPOINTS = {
    "static",
    "main.index",
    "main.features",
    "main.manifest",
    "main.service_worker",
    "main.offline",
    "mpesa.callback",
}


@billing_bp.before_app_request
def require_active_subscription():
    """Send signed-in users of an unpaid shop to the plans page. Their records are kept."""
    if (
        request.endpoint is None
        or request.endpoint in OPEN_ENDPOINTS
        or request.blueprint in OPEN_BLUEPRINTS
        or not current_user.is_authenticated
        or current_user.has_access
    ):
        return None
    if request.is_json or request.path.startswith("/transactions/api/"):
        # Offline sales stay queued in the browser until the shop renews.
        return jsonify(error="Your Duka Credit subscription has ended.", results=[]), 402
    return redirect(url_for("billing.plans"))


@billing_bp.route("/")
@login_required
def plans():
    shop = shop_of(current_user)
    plan = request.args.get("plan", DEFAULT_PLAN)
    form = SubscribeForm(plan=plan, phone_number=shop.phone_number)
    return _plans_page(shop, form, PaybillClaimForm(plan=plan))


def _plans_page(shop, form, claim_form=None):
    payments = (
        shop.subscription_payments.filter(SubscriptionPayment.status == "paid")
        .order_by(SubscriptionPayment.completed_at.desc())
        .limit(12)
        .all()
    )
    # Codes still being checked, and any turned down this past week.
    claims = (
        shop.subscription_payments.filter(
            SubscriptionPayment.method == "paybill",
            db.or_(
                SubscriptionPayment.status == "pending",
                db.and_(
                    SubscriptionPayment.status == "failed",
                    SubscriptionPayment.completed_at > datetime.utcnow() - timedelta(days=7),
                ),
            ),
        )
        .order_by(SubscriptionPayment.created_at.desc())
        .all()
    )
    return render_template(
        "billing/plans.html",
        shop=shop,
        form=form,
        claim_form=claim_form or PaybillClaimForm(plan=form.plan.data),
        claims=claims,
        paybill=paybill_details(),
        plans=list(PLANS.values()),
        payments=payments,
        trial_days=TRIAL_DAYS,
        days_left=days_left(shop.access_ends_at),
        prompts_ready=billing_client() is not None,
        pay_instructions=current_app.config.get("BILLING_PAY_INSTRUCTIONS", ""),
        support_phone=current_app.config.get("BILLING_SUPPORT_PHONE", ""),
    )


@billing_bp.route("/subscribe", methods=["POST"])
@login_required
@admin_required
def subscribe():
    shop = current_user
    form = SubscribeForm()
    if shop.billing_exempt:
        flash("This shop uses Duka Credit free of charge. There is nothing to pay.", "info")
        return redirect(url_for("billing.plans"))
    if not form.validate_on_submit():
        for errors in form.errors.values():
            flash(errors[0], "danger")
        return _plans_page(shop, form), 400

    client = billing_client()
    if client is None:
        flash("M-Pesa prompts for subscriptions are not switched on yet. Pay using the steps below.", "warning")
        return redirect(url_for("billing.plans", plan=form.plan.data))

    waiting = shop.subscription_payments.filter(
        SubscriptionPayment.method == "mpesa_stk",
        SubscriptionPayment.status == "pending",
        SubscriptionPayment.created_at > datetime.utcnow() - PROMPT_COOLDOWN,
    ).first()
    if waiting:
        flash("A payment prompt was just sent. Finish or cancel it on your phone first.", "warning")
        return redirect(url_for("billing.payment_status", payment_id=waiting.subscription_payment_id))

    plan = PLANS[form.plan.data]
    phone = safaricom_msisdn(form.phone_number.data)
    token = secrets.token_urlsafe(32)
    callback_url = (callback_base_url() or UNREACHABLE_CALLBACK_BASE) + url_for(
        "billing.callback", token=token
    )
    try:
        response = client.stk_push(
            phone,
            plan.price,
            account_reference_for(shop.business_name),
            "Duka Credit",
            callback_url,
        )
    except DarajaError as exc:
        flash(str(exc), "danger")
        return redirect(url_for("billing.plans", plan=plan.code))

    payment = SubscriptionPayment(
        retailer_id=shop.retailer_id,
        plan=plan.code,
        amount=plan.price,
        method="mpesa_stk",
        phone_number=phone,
        callback_token=token,
        merchant_request_id=response.get("MerchantRequestID"),
        checkout_request_id=response.get("CheckoutRequestID"),
    )
    db.session.add(payment)
    db.session.commit()
    return redirect(url_for("billing.payment_status", payment_id=payment.subscription_payment_id))


@billing_bp.route("/claim", methods=["POST"])
@login_required
@admin_required
def claim_paybill_payment():
    """The shop paid the Paybill by hand and sends its M-Pesa code for checking."""
    shop = current_user
    form = PaybillClaimForm()
    if shop.billing_exempt or paybill_details() is None:
        return redirect(url_for("billing.plans"))
    if not form.validate_on_submit():
        for errors in form.errors.values():
            flash(errors[0], "danger")
        return _plans_page(shop, SubscribeForm(plan=form.plan.data, phone_number=shop.phone_number), form), 400
    if SubscriptionPayment.query.filter_by(mpesa_receipt=form.mpesa_code.data).first():
        flash("That M-Pesa code has already been sent.", "warning")
        return redirect(url_for("billing.plans", plan=form.plan.data))

    plan = PLANS[form.plan.data]
    db.session.add(
        SubscriptionPayment(
            retailer_id=shop.retailer_id,
            plan=plan.code,
            amount=plan.price,
            method="paybill",
            mpesa_receipt=form.mpesa_code.data,
        )
    )
    db.session.commit()
    flash(
        f"Thank you. We will check M-Pesa code {form.mpesa_code.data} and start your "
        f"{plan.name} plan as soon as the payment is confirmed.",
        "success",
    )
    return redirect(url_for("billing.plans"))


def _own_payment_or_404(payment_id):
    """One of this shop's M-Pesa prompt payments."""
    return SubscriptionPayment.query.filter_by(
        subscription_payment_id=payment_id, retailer_id=current_user.retailer_id, method="mpesa_stk"
    ).first_or_404()


def _status_payload(payment, message=None):
    messages = {
        "pending": "Waiting for you to enter your M-Pesa PIN on your phone.",
        "paid": (
            f"Paid. Duka Credit is yours until {payment.period_end:%d %b %Y}."
            if payment.period_end
            else "Paid."
        ),
        "cancelled": payment.result_desc or "The payment was not made.",
        "failed": payment.result_desc or "The payment did not go through.",
    }
    return {
        "status": payment.status,
        "message": message or messages[payment.status],
        "receipt": payment.mpesa_receipt,
    }


@billing_bp.route("/payments/<int:payment_id>")
@login_required
@admin_required
def payment_status(payment_id):
    payment = _own_payment_or_404(payment_id)
    return render_template(
        "billing/payment_status.html",
        payment=payment,
        plan=PLANS.get(payment.plan),
        initial=_status_payload(payment),
    )


@billing_bp.route("/payments/<int:payment_id>/status")
@login_required
@admin_required
def payment_status_json(payment_id):
    """Polled by the waiting page; asks Safaricom when the callback has not arrived."""
    payment = _own_payment_or_404(payment_id)
    if payment.status != "pending":
        return jsonify(_status_payload(payment))

    if datetime.utcnow() - payment.created_at > PROMPT_EXPIRY:
        finish_subscription_payment(payment, "1037", "No answer.")
        db.session.commit()
        return jsonify(_status_payload(payment))

    client = billing_client()
    if client is None:
        return jsonify(_status_payload(payment))
    try:
        result_code, result_desc = client.stk_query(payment.checkout_request_id)
    except DarajaError as exc:
        return jsonify(_status_payload(payment, message=str(exc)))
    if result_code is not None:
        finish_subscription_payment(payment, result_code, result_desc)
        db.session.commit()
    return jsonify(_status_payload(payment))


@billing_bp.route("/callback/<token>", methods=["POST"])
def callback(token):
    """Safaricom reports the subscription prompt result here. Always acknowledge."""
    accepted = jsonify(ResultCode=0, ResultDesc="Accepted")
    payment = SubscriptionPayment.query.filter_by(callback_token=token).first()
    body = (request.get_json(silent=True) or {}).get("Body", {}).get("stkCallback", {})
    if payment is None or not body or body.get("CheckoutRequestID") != payment.checkout_request_id:
        current_app.logger.warning("Ignored a subscription callback that matched no prompt.")
        return accepted

    items = {
        item.get("Name"): item.get("Value")
        for item in body.get("CallbackMetadata", {}).get("Item", [])
    }
    finish_subscription_payment(
        payment,
        body.get("ResultCode"),
        body.get("ResultDesc"),
        receipt=items.get("MpesaReceiptNumber"),
    )
    db.session.commit()
    return accepted
