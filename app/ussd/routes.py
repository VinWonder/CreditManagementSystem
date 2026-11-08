"""USSD: the Africa's Talking callback, the owner's PIN page and a simulator."""

import secrets

from flask import Blueprint, abort, current_app, flash, jsonify, make_response, redirect, render_template, request, url_for
from flask_login import current_user, login_required
from flask_wtf import FlaskForm
from wtforms import PasswordField, SubmitField
from wtforms.validators import DataRequired, EqualTo, Regexp

from app.extensions import db
from app.models import Customer
from app.permissions import admin_required
from app.ussd.menu import e164, end, respond

ussd_bp = Blueprint("ussd", __name__, url_prefix="/ussd")

PIN = Regexp(r"^\d{4}$", message="Use exactly 4 digits.")
WEAK_PINS = {"0000", "1111", "2222", "3333", "4444", "5555", "6666", "7777", "8888", "9999", "1234", "4321", "1212"}


class UssdPinForm(FlaskForm):
    current_password = PasswordField("Current password", validators=[DataRequired()])
    pin = PasswordField("New USSD PIN", validators=[DataRequired(), PIN])
    confirm_pin = PasswordField(
        "Confirm USSD PIN", validators=[DataRequired(), EqualTo("pin", message="The PINs do not match.")]
    )
    submit = SubmitField("Save PIN")


def _token_ok(token):
    expected = current_app.config.get("USSD_CALLBACK_TOKEN") or ""
    return bool(expected) and secrets.compare_digest(token, expected)


def _text_reply(body):
    response = make_response(body)
    response.mimetype = "text/plain"
    return response


@ussd_bp.route("/callback/<token>", methods=["POST"])
def callback(token):
    """Africa's Talking posts sessionId, serviceCode, phoneNumber and text."""
    if not _token_ok(token):
        abort(404)
    phone = e164(request.values.get("phoneNumber", ""))
    if phone is None:
        return _text_reply(end("Sorry, we could not read your phone number."))
    try:
        return _text_reply(respond(phone, request.values.get("text", "")))
    except Exception:  # noqa: BLE001 - a caller must always get a screen back
        db.session.rollback()
        current_app.logger.exception("USSD request failed")
        return _text_reply(end("Sorry, something went wrong. Please try again."))


@ussd_bp.route("/pin", methods=["GET", "POST"])
@login_required
@admin_required
def set_pin():
    form = UssdPinForm()
    if form.validate_on_submit():
        if not current_user.check_password(form.current_password.data):
            form.current_password.errors.append("That is not your current password.")
        elif form.pin.data in WEAK_PINS:
            form.pin.errors.append("That PIN is too easy to guess. Choose another.")
        else:
            current_user.set_ussd_pin(form.pin.data)
            db.session.commit()
            flash("USSD PIN saved.", "success")
            return redirect(url_for("ussd.simulator"))
    return render_template(
        "ussd/pin.html", form=form, service_code=current_app.config.get("USSD_SERVICE_CODE")
    )


@ussd_bp.route("/simulator")
@login_required
@admin_required
def simulator():
    customers = (
        Customer.query.filter_by(retailer_id=current_user.retailer_id)
        .order_by(Customer.full_name.asc())
        .all()
    )
    return render_template(
        "ussd/simulator.html",
        customers=customers,
        service_code=current_app.config.get("USSD_SERVICE_CODE") or "*384*000#",
        live=bool(current_app.config.get("USSD_CALLBACK_TOKEN")),
    )


@ussd_bp.route("/simulator/send", methods=["POST"])
@login_required
@admin_required
def simulate():
    """Run the real menu, limited to this shop, as the owner or one of its customers."""
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return jsonify(error="A JSON object is required."), 400
    text = payload.get("text") or ""
    if not isinstance(text, str) or len(text) > 200:
        return jsonify(error="Invalid input."), 400
    if payload.get("as") == "customer":
        customer = Customer.query.filter_by(
            retailer_id=current_user.retailer_id, customer_id=payload.get("customer_id")
        ).first()
        if customer is None:
            return jsonify(error="Choose one of your customers."), 400
        phone = customer.phone_number
    else:
        phone = current_user.phone_number
    reply = respond(phone, text, only_retailer_id=current_user.retailer_id)
    return jsonify(reply=reply)
