"""Developer commands for connecting each shop's Paybill or Till to M-Pesa prompts.

    flask --app run mpesa list
    flask --app run mpesa set <shop> --environment production --type paybill --shortcode 123456
    flask --app run mpesa test <shop>
    flask --app run mpesa disable <shop> | enable <shop> | remove <shop>

<shop> is the retailer id, phone number or email. Keys and the passkey are asked
for without being shown, so they never land in shell history.
"""

import re
from datetime import datetime

import click
from flask.cli import AppGroup

from app.extensions import db
from app.models import MpesaAccount, Retailer
from app.mpesa.daraja import DarajaClient, DarajaError
from app.reminders.credentials import MessagingCredentialError, encrypt_secret

mpesa_cli = AppGroup("mpesa", help="Connect shops' Paybill or Till numbers to M-Pesa prompts.")

SHORTCODE = re.compile(r"^\d{5,7}$")


def find_retailer(identifier):
    identifier = identifier.strip()
    if identifier.isdigit() and len(identifier) < 9:
        retailer = db.session.get(Retailer, int(identifier))
    elif "@" in identifier:
        retailer = Retailer.query.filter_by(email=identifier.lower()).first()
    else:
        digits = re.sub(r"\D", "", identifier)
        if digits.startswith("0"):
            digits = "254" + digits[1:]
        retailer = Retailer.query.filter_by(phone_number=f"+{digits}").first()
    if retailer is None:
        raise click.ClickException(
            f"No shop matches '{identifier}'. Use 'flask mpesa list' to see retailer ids."
        )
    return retailer


def _describe(account):
    if account is None:
        return "not connected"
    kind = f"Till {account.till_number} (store {account.shortcode})" if account.account_type == "till" else f"Paybill {account.shortcode}"
    state = "" if account.active else ", DISABLED"
    return f"{account.environment}, {kind}{state}"


@mpesa_cli.command("list")
def list_accounts():
    """Show every shop and whether M-Pesa prompts are connected."""
    retailers = Retailer.query.order_by(Retailer.retailer_id).all()
    if not retailers:
        click.echo("No shops yet.")
        return
    for retailer in retailers:
        click.echo(
            f"{retailer.retailer_id:>4}  {retailer.business_name:<28.28}  "
            f"{retailer.phone_number:<14}  {_describe(retailer.mpesa_account)}"
        )


@mpesa_cli.command("set")
@click.argument("shop")
@click.option("--environment", type=click.Choice(["sandbox", "production"]), required=True)
@click.option("--type", "account_type", type=click.Choice(["paybill", "till"]), required=True)
@click.option("--shortcode", required=True, help="Paybill number, or the store (head office) number for a Till.")
@click.option("--till-number", default="", help="For --type till: the till number customers pay to.")
@click.option("--consumer-key", prompt=True, hide_input=True)
@click.option("--consumer-secret", prompt=True, hide_input=True)
@click.option("--passkey", prompt=True, hide_input=True)
@click.option("--skip-check", is_flag=True, help="Save without signing in to Safaricom first.")
def set_account(shop, environment, account_type, shortcode, till_number,
                consumer_key, consumer_secret, passkey, skip_check):
    """Connect or update a shop's Paybill or Till."""
    retailer = find_retailer(shop)
    shortcode, till_number = shortcode.strip(), till_number.strip()
    consumer_key, consumer_secret, passkey = consumer_key.strip(), consumer_secret.strip(), passkey.strip()
    if not SHORTCODE.match(shortcode):
        raise click.BadParameter("Use the 5-7 digit number from Safaricom.", param_hint="--shortcode")
    if account_type == "till":
        if not SHORTCODE.match(till_number):
            raise click.BadParameter("A Till needs its 5-7 digit till number.", param_hint="--till-number")
    else:
        till_number = ""
    if not (consumer_key and consumer_secret and passkey):
        raise click.ClickException("The consumer key, consumer secret and passkey are all required.")

    if not skip_check:
        client = DarajaClient(
            environment=environment,
            account_type=account_type,
            shortcode=shortcode,
            till_number=till_number,
            consumer_key=consumer_key,
            consumer_secret=consumer_secret,
            passkey=passkey,
        )
        try:
            client.check_credentials()
        except DarajaError as exc:
            raise click.ClickException(f"Not saved: {exc}")
        click.echo(f"Safaricom {environment} accepted the consumer key and secret.")

    try:
        encrypted = {
            "consumer_key_encrypted": encrypt_secret(consumer_key),
            "consumer_secret_encrypted": encrypt_secret(consumer_secret),
            "passkey_encrypted": encrypt_secret(passkey),
        }
    except MessagingCredentialError as exc:
        raise click.ClickException(f"{exc} Set MESSAGING_CREDENTIALS_ENCRYPTION_KEY in .env.")

    account = retailer.mpesa_account or MpesaAccount(retailer_id=retailer.retailer_id)
    account.environment = environment
    account.account_type = account_type
    account.shortcode = shortcode
    account.till_number = till_number or None
    account.active = True
    account.updated_at = datetime.utcnow()
    for name, value in encrypted.items():
        setattr(account, name, value)
    db.session.add(account)
    db.session.commit()
    click.echo(f"{retailer.business_name}: {_describe(account)}. M-Pesa prompts are on.")
    click.echo("The passkey is only checked when the first prompt is sent; send a KES 1 test prompt.")


@mpesa_cli.command("test")
@click.argument("shop")
def test_account(shop):
    """Sign in to Safaricom with a shop's saved keys."""
    retailer = find_retailer(shop)
    try:
        DarajaClient.for_account(retailer.mpesa_account).check_credentials()
    except DarajaError as exc:
        raise click.ClickException(str(exc))
    click.echo(f"{retailer.business_name}: Safaricom accepted the keys ({_describe(retailer.mpesa_account)}).")


def _set_active(shop, active):
    retailer = find_retailer(shop)
    if retailer.mpesa_account is None:
        raise click.ClickException(f"{retailer.business_name} has no M-Pesa account to change.")
    retailer.mpesa_account.active = active
    db.session.commit()
    click.echo(f"{retailer.business_name}: M-Pesa prompts {'on' if active else 'off'}.")


@mpesa_cli.command("disable")
@click.argument("shop")
def disable_account(shop):
    """Stop a shop from sending prompts, keeping its saved keys."""
    _set_active(shop, False)


@mpesa_cli.command("enable")
@click.argument("shop")
def enable_account(shop):
    """Let a shop send prompts again."""
    _set_active(shop, True)


@mpesa_cli.command("remove")
@click.argument("shop")
@click.confirmation_option(prompt="Delete this shop's M-Pesa keys?")
def remove_account(shop):
    """Delete a shop's M-Pesa account and keys."""
    retailer = find_retailer(shop)
    if retailer.mpesa_account is None:
        raise click.ClickException(f"{retailer.business_name} has no M-Pesa account.")
    db.session.delete(retailer.mpesa_account)
    db.session.commit()
    click.echo(f"{retailer.business_name}: M-Pesa account removed.")
