"""Developer commands for retailer accounts.

    flask --app run accounts purge-unverified [--dry-run]
"""

import click
from flask import current_app
from flask.cli import AppGroup

from app.auth.signups import purge_abandoned_signups

accounts_cli = AppGroup("accounts", help="Manage retailer accounts.")


@accounts_cli.command("purge-unverified")
@click.option("--dry-run", is_flag=True, help="List the sign-ups without deleting them.")
def purge_unverified(dry_run):
    """Delete sign-ups whose email was never verified."""
    hours = current_app.config.get("UNVERIFIED_SIGNUP_HOURS", 24)
    abandoned = purge_abandoned_signups(dry_run=dry_run)
    if not abandoned:
        click.echo(f"No sign-ups have been unverified for more than {hours} hours.")
        return
    for retailer in abandoned:
        click.echo(f"  {retailer.business_name} ({retailer.phone_number}, {retailer.email}), signed up {retailer.created_at:%d %b %Y %H:%M}")
    verb = "Would delete" if dry_run else "Deleted"
    click.echo(f"{verb} {len(abandoned)} sign-up(s) unverified for more than {hours} hours.")
