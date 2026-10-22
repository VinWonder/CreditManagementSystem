"""Developer commands for retailer accounts.

    flask --app run accounts list
    flask --app run accounts purge-unverified [--all] [--dry-run]
    flask --app run accounts delete <shop>
"""

import click
from flask import current_app
from flask.cli import AppGroup

from app.auth.signups import purge_abandoned_signups
from app.extensions import db
from app.models import Retailer
from app.mpesa.cli import find_retailer

accounts_cli = AppGroup("accounts", help="Manage retailer accounts.")


@accounts_cli.command("list")
def list_accounts():
    """Show every shop with its phone, email and whether the email is verified."""
    retailers = Retailer.query.order_by(Retailer.retailer_id).all()
    if not retailers:
        click.echo("No shops yet.")
        return
    for retailer in retailers:
        verified = "verified" if retailer.email_verified_at or not retailer.email else "UNVERIFIED"
        click.echo(
            f"{retailer.retailer_id:>4}  {retailer.business_name:<28.28}  {retailer.phone_number:<14}  "
            f"{(retailer.email or '-'):<32.32}  {verified}"
        )


@accounts_cli.command("purge-unverified")
@click.option("--all", "any_age", is_flag=True, help="Include sign-ups less than UNVERIFIED_SIGNUP_HOURS old.")
@click.option("--dry-run", is_flag=True, help="List the sign-ups without deleting them.")
def purge_unverified(any_age, dry_run):
    """Delete sign-ups whose email was never verified."""
    hours = current_app.config.get("UNVERIFIED_SIGNUP_HOURS", 24)
    abandoned = purge_abandoned_signups(dry_run=dry_run, any_age=any_age)
    age = "unverified" if any_age else f"unverified for more than {hours} hours"
    if not abandoned:
        click.echo(f"No sign-ups are {age}.")
        return
    for retailer in abandoned:
        click.echo(f"  {retailer.business_name} ({retailer.phone_number}, {retailer.email}), signed up {retailer.created_at:%d %b %Y %H:%M}")
    verb = "Would delete" if dry_run else "Deleted"
    click.echo(f"{verb} {len(abandoned)} sign-up(s) {age}.")


@accounts_cli.command("delete")
@click.argument("shop")
@click.confirmation_option(prompt="Delete this shop and ALL its customers, sales and payments?")
def delete_account(shop):
    """Delete one shop and everything recorded under it (for test accounts)."""
    retailer = find_retailer(shop)
    name = retailer.business_name
    db.session.delete(retailer)
    db.session.commit()
    click.echo(f"Deleted {name} and all its records.")
