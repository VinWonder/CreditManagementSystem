"""Developer commands for shop subscriptions.

    flask --app run billing list
    flask --app run billing free <shop> [--remove]
    flask --app run billing activate <shop> <plan> [--receipt CODE]
    flask --app run billing extend-trial <shop> --days 7

<shop> is the shop name, retailer id, phone number or email.
"""

from datetime import datetime, timedelta

import click
from flask.cli import AppGroup

from app.billing.plans import PLANS
from app.billing.service import days_left, grant_plan
from app.extensions import db
from app.models import Retailer, SubscriptionPayment
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
