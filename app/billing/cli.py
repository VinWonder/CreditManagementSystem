"""Developer commands for shop subscriptions.

    flask --app run billing list
    flask --app run billing free <shop> [--remove]
    flask --app run billing activate <shop> <plan> [--receipt CODE]
    flask --app run billing extend-trial <shop> --days 7
    flask --app run billing pending
    flask --app run billing confirm <mpesa code>
    flask --app run billing reject <mpesa code>
    flask --app run billing register-c2b

<shop> is the shop name, retailer id, phone number or email.
"""

from datetime import datetime, timedelta

import click
from flask import current_app, url_for
from flask.cli import AppGroup

from app.billing.plans import PLANS
from app.billing.service import billing_client, confirm_paybill_claim, days_left, grant_plan
from app.extensions import db
from app.models import C2BReceipt, Retailer, SubscriptionPayment
from app.mpesa.daraja import DarajaError
from app.mpesa.routes import callback_base_url
from app.mpesa.cli import find_retailer

billing_cli = AppGroup("billing", help="Manage shop subscriptions.")


def find_shop(identifier):
    """A shop by its name (any capitals), or by id, phone or email."""
    name = " ".join(identifier.split()).lower()
    retailer = Retailer.query.filter(db.func.lower(Retailer.business_name) == name).first()
    return retailer or find_retailer(identifier)


def describe(retailer):
    status = retailer.subscription_status
    if status == "free":
        return "FREE FOREVER"
    if status == "expired":
        ended = retailer.access_ends_at
        return f"EXPIRED {ended:%d %b %Y}" if ended else "EXPIRED"
    ends = retailer.paid_until if status == "active" else retailer.trial_ends_at
    return f"{status} until {ends:%d %b %Y} ({days_left(ends)} days left)"


@billing_cli.command("list")
def list_subscriptions():
    """Show every shop and where its subscription stands."""
    retailers = Retailer.query.order_by(Retailer.retailer_id).all()
    if not retailers:
        click.echo("No shops yet.")
        return
    for retailer in retailers:
        click.echo(f"{retailer.retailer_id:>4}  {retailer.business_name:<28.28}  {describe(retailer)}")


@billing_cli.command("free")
@click.argument("shop")
@click.option("--remove", is_flag=True, help="Make the shop pay again (its trial or paid time still counts).")
def free_forever(shop, remove):
    """Let a shop use Duka Credit free of charge, forever."""
    retailer = find_shop(shop)
    retailer.billing_exempt = not remove
    db.session.commit()
    click.echo(f"{retailer.business_name}: {describe(retailer)}")


@billing_cli.command("activate")
@click.argument("shop")
@click.argument("plan", type=click.Choice(sorted(PLANS)))
@click.option("--receipt", default="", help="M-Pesa or bank reference for the money received.")
def activate(shop, plan, receipt):
    """Record a subscription paid outside the app (for example to the Till) and extend the shop."""
    retailer = find_shop(shop)
    chosen = PLANS[plan]
    now = datetime.utcnow()
    payment = SubscriptionPayment(
        retailer_id=retailer.retailer_id,
        plan=chosen.code,
        amount=chosen.price,
        method="manual",
        status="paid",
        mpesa_receipt=receipt.strip().upper() or None,
        completed_at=now,
    )
    db.session.add(payment)
    grant_plan(retailer, payment, now=now)
    if payment.mpesa_receipt:
        receipt = C2BReceipt.query.filter_by(trans_id=payment.mpesa_receipt).first()
        if receipt is not None:
            receipt.subscription_payment = payment
    db.session.commit()
    click.echo(
        f"Recorded {chosen.name} (KES {chosen.price:,}) for {retailer.business_name}: {describe(retailer)}"
    )


@billing_cli.command("extend-trial")
@click.argument("shop")
@click.option("--days", type=click.IntRange(1, 365), default=7, show_default=True)
def extend_trial(shop, days):
    """Give a shop more free days, counted from today or the end of its current trial."""
    retailer = find_shop(shop)
    now = datetime.utcnow()
    start = retailer.trial_ends_at if retailer.trial_ends_at and retailer.trial_ends_at > now else now
    retailer.trial_ends_at = start + timedelta(days=days)
    db.session.commit()
    click.echo(f"{retailer.business_name}: {describe(retailer)}")


def find_claim(code):
    payment = SubscriptionPayment.query.filter_by(
        mpesa_receipt=code.strip().upper(), method="paybill", status="pending"
    ).first()
    if payment is None:
        raise click.ClickException(f"No M-Pesa code '{code}' is waiting. See 'flask billing pending'.")
    return payment


@billing_cli.command("pending")
def pending_claims():
    """M-Pesa codes shops sent after paying the Paybill, waiting to be checked."""
    claims = (
        SubscriptionPayment.query.filter_by(method="paybill", status="pending")
        .order_by(SubscriptionPayment.created_at)
        .all()
    )
    unmatched = C2BReceipt.query.filter_by(subscription_payment_id=None).order_by(C2BReceipt.received_at).all()
    if not claims and not unmatched:
        click.echo("No codes waiting.")
        return
    for receipt in unmatched:
        click.echo(
            f"{receipt.trans_id:<12}  KES {receipt.amount:>7,}  PAID, NO SHOP  account '{receipt.bill_ref or '-'}'  "
            f"from {receipt.payer_name or '-'}  (fix: billing activate <shop> <plan> --receipt {receipt.trans_id})"
        )
    for claim in claims:
        click.echo(
            f"{claim.mpesa_receipt:<12}  KES {claim.amount:>7,}  {PLANS[claim.plan].name:<9}  "
            f"{claim.retailer.business_name:<28.28}  sent {claim.created_at:%d %b %H:%M} UTC"
        )


@billing_cli.command("confirm")
@click.argument("code")
def confirm_claim(code):
    """The code is on your bank statement for the right amount: start the shop's plan."""
    payment = find_claim(code)
    confirm_paybill_claim(payment)
    db.session.commit()
    click.echo(f"Confirmed {payment.mpesa_receipt} for {payment.retailer.business_name}: {describe(payment.retailer)}")


@billing_cli.command("reject")
@click.argument("code")
def reject_claim(code):
    """The code is not on your statement (or the amount is wrong): drop it."""
    payment = find_claim(code)
    payment.status = "failed"
    payment.result_desc = "Not found on the bank statement."
    payment.completed_at = datetime.utcnow()
    db.session.commit()
    click.echo(f"Rejected {payment.mpesa_receipt} from {payment.retailer.business_name}.")


@billing_cli.command("register-c2b")
def register_c2b():
    """Ask Safaricom to report every payment to the app's own Paybill (run once after going live)."""
    client = billing_client()
    token = current_app.config.get("BILLING_C2B_TOKEN")
    base = callback_base_url()
    if client is None or not token or not base:
        raise click.ClickException(
            "Set BILLING_MPESA_* keys, BILLING_C2B_TOKEN and PUBLIC_BASE_URL (https) first."
        )
    with current_app.test_request_context(base_url=base):
        confirmation = base + url_for("billing.c2b_confirmation", token=token)
        validation = base + url_for("billing.c2b_validation", token=token)
    try:
        client.register_c2b_urls(confirmation, validation)
    except DarajaError as exc:
        raise click.ClickException(str(exc)) from exc
    click.echo(f"Safaricom will report payments to Paybill {client.shortcode} at {base}/billing/c2b/...")
