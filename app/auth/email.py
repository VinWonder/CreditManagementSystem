"""Outgoing account email (verification codes) over SMTP."""

import smtplib
import ssl
from email.message import EmailMessage

import click
from flask import current_app


def send_email(to_address, subject, body):
    """Send a plain-text email. Returns (success, info).

    Without SMTP settings, development simulates the send (callers log the
    code to the terminal) and production refuses rather than pretending.
    """
    config = current_app.config
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
    try:
        if port == 465:
            server = smtplib.SMTP_SSL(host, port, timeout=15, context=context)
        else:
            server = smtplib.SMTP(host, port, timeout=15)
        with server:
            if port != 465 and config.get("SMTP_USE_TLS", True):
                server.starttls(context=context)
            if config.get("SMTP_USERNAME"):
                server.login(config["SMTP_USERNAME"], config["SMTP_PASSWORD"])
            server.send_message(message)
    except smtplib.SMTPAuthenticationError:
        reason = (
            "SMTP login was refused. Check SMTP_USERNAME and SMTP_PASSWORD; "
            "Gmail and Outlook need an app password, not the normal account password."
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
def send_test_email_command(to_address):
    """Send a test email to check the SMTP settings in .env."""
    config = current_app.config
    if not config.get("SMTP_HOST"):
        click.echo("SMTP_HOST is empty in .env, so the app cannot send email.")
        click.echo("Fill in SMTP_HOST, SMTP_PORT, SMTP_USERNAME, SMTP_PASSWORD and MAIL_FROM.")
        raise SystemExit(1)

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
