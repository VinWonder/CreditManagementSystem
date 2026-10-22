"""Outgoing account email (verification codes) over SMTP or the Brevo email API.

Hosts such as Render's free plan block outgoing SMTP ports; Brevo sends over
HTTPS instead, so set BREVO_API_KEY there.
"""

import smtplib
import ssl
from email.message import EmailMessage
from email.utils import parseaddr

import click
import requests
from flask import current_app
from flask.cli import with_appcontext


def email_configured(config):
    return bool(config.get("BREVO_API_KEY") or config.get("SMTP_HOST"))


def _send_with_brevo(to_address, subject, body):
    config = current_app.config
    name, address = parseaddr(config.get("MAIL_FROM") or config.get("SMTP_USERNAME") or "")
    if not address:
        return False, "Set MAIL_FROM to the sender address verified in Brevo"
    try:
        response = requests.post(
            "https://api.brevo.com/v3/smtp/email",
            headers={"api-key": config["BREVO_API_KEY"], "accept": "application/json"},
            json={
                "sender": {"name": name or "Duka Credit", "email": address},
                "to": [{"email": to_address}],
                "subject": subject,
                "textContent": body,
            },
            timeout=15,
        )
    except requests.RequestException as exc:
        reason = f"Could not reach Brevo ({type(exc).__name__})."
    else:
        if response.status_code in (200, 201, 202):
            return True, "sent"
        try:
            detail = response.json().get("message", "")
        except ValueError:
            detail = ""
        if response.status_code == 401:
            reason = "Brevo refused the API key. Check BREVO_API_KEY."
        else:
            reason = (
                f"Brevo did not send the email ({response.status_code}: {detail}). "
                f"Make sure {address} is a verified sender in Brevo."
            )
    current_app.logger.warning("Email to %s failed: %s", to_address, reason)
    return False, reason


def send_email(to_address, subject, body):
    """Send a plain-text email. Returns (success, info).

    Without SMTP settings, development simulates the send (callers log the
    code to the terminal) and production refuses rather than pretending.
    """
    config = current_app.config
    if config.get("BREVO_API_KEY"):
        return _send_with_brevo(to_address, subject, body)
    if not config.get("SMTP_HOST"):
        if config["APP_ENV"] == "production":
            return False, "Email is not configured for production"
        return True, "simulated"

    sender = config.get("MAIL_FROM") or config.get("SMTP_USERNAME")
    if not sender:
        return False, "Set MAIL_FROM or SMTP_USERNAME to send email"

    message = EmailMessage()
    message["From"] = sender
    message["To"] = to_address
    message["Subject"] = subject
    message.set_content(body)

    host, port = config["SMTP_HOST"], config["SMTP_PORT"]
    context = ssl.create_default_context()
    login_refused = (
        f"SMTP login as {config.get('SMTP_USERNAME')} was refused. SMTP_USERNAME must be "
        "the account the app password was created for, and Gmail and Outlook need an "
        "app password, not the normal account password."
    )
    stage = "connect"
    try:
        if port == 465:
            server = smtplib.SMTP_SSL(host, port, timeout=15, context=context)
        else:
            server = smtplib.SMTP(host, port, timeout=15)
        with server:
            if port != 465 and config.get("SMTP_USE_TLS", True):
                server.starttls(context=context)
            if config.get("SMTP_USERNAME"):
                stage = "login"
                server.login(config["SMTP_USERNAME"], config["SMTP_PASSWORD"])
            stage = "send"
            server.send_message(message)
    except smtplib.SMTPAuthenticationError:
        reason = login_refused
    except smtplib.SMTPServerDisconnected as exc:
        # Gmail hangs up instead of answering 535 when the username and the
        # app password belong to different accounts.
        reason = login_refused if stage == "login" else (
            f"The mail server closed the connection ({exc})."
        )
    except smtplib.SMTPRecipientsRefused:
        reason = f"The mail server refused the recipient address {to_address}."
    except smtplib.SMTPSenderRefused:
        reason = "The mail server refused MAIL_FROM. Use the address you sign in to SMTP with."
    except smtplib.SMTPException as exc:
        reason = f"The mail server rejected the message ({type(exc).__name__}: {exc})."
    except OSError as exc:
        reason = f"Could not connect to {host}:{port} ({type(exc).__name__}: {exc})."
    else:
        return True, "sent"
    current_app.logger.warning("Email to %s failed: %s", to_address, reason)
    return False, reason


@click.command("send-test-email")
@click.argument("to_address")
@with_appcontext
def send_test_email_command(to_address):
    """Send a test email to check the SMTP settings in .env."""
    config = current_app.config
    if not email_configured(config):
        click.echo("SMTP_HOST is empty in .env, so the app cannot send email.")
        click.echo("Fill in SMTP_HOST, SMTP_PORT, SMTP_USERNAME, SMTP_PASSWORD and MAIL_FROM,")
        click.echo("or BREVO_API_KEY and MAIL_FROM where SMTP ports are blocked.")
        raise SystemExit(1)

    if config.get("BREVO_API_KEY"):
        click.echo(f"Sending through Brevo as {config.get('MAIL_FROM')}...")
    else:
        click.echo(
            f"Sending through {config['SMTP_HOST']}:{config['SMTP_PORT']} "
            f"as {config.get('SMTP_USERNAME') or '(no login)'}..."
        )
    sent, info = send_email(
        to_address,
        "Duka Credit test email",
        "This is a test from Duka Credit. Email verification codes can be delivered.\n",
    )
    if not sent:
        click.echo(f"Failed: {info}")
        raise SystemExit(1)
    click.echo(f"Sent. Check the inbox (and spam folder) of {to_address}.")
