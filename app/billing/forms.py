import re

from flask_wtf import FlaskForm
from wtforms import HiddenField, StringField, SubmitField
from wtforms.validators import DataRequired, Length, ValidationError

from app.billing.plans import PLANS
from app.mpesa.daraja import safaricom_msisdn


class SubscribeForm(FlaskForm):
    plan = HiddenField("Plan", validators=[DataRequired()])
    phone_number = StringField(
        "M-Pesa number",
        validators=[DataRequired(message="Enter the Safaricom number that will pay."), Length(max=20)],
    )
    submit = SubmitField("Pay with M-Pesa")

    def validate_plan(self, field):
        if field.data not in PLANS:
            raise ValidationError("Choose one of the plans.")

    def validate_phone_number(self, field):
        if not safaricom_msisdn(field.data):
            raise ValidationError("Enter a Safaricom number, for example 0712 345 678.")


class PaybillClaimForm(FlaskForm):
    """The M-Pesa code a shop got after paying the subscription to the Paybill."""

    plan = HiddenField("Plan", validators=[DataRequired()])
    mpesa_code = StringField(
        "M-Pesa code",
        validators=[DataRequired(message="Enter the code from the M-Pesa message."), Length(max=20)],
    )
    submit = SubmitField("Send code")

    def validate_plan(self, field):
        if field.data not in PLANS:
            raise ValidationError("Choose one of the plans.")

    def validate_mpesa_code(self, field):
        code = re.sub(r"\s", "", field.data or "").upper()
        if not re.fullmatch(r"[A-Z0-9]{10}", code):
            raise ValidationError("An M-Pesa code is 10 letters and numbers, for example SKL4ABC123.")
        field.data = code
