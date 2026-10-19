import re

from flask_wtf import FlaskForm
from wtforms import IntegerField, TextAreaField, SelectField, BooleanField, SubmitField
from wtforms.validators import (
    DataRequired,
    InputRequired,
    NumberRange,
    Length,
    ValidationError,
)


SUPPORTED_PLACEHOLDERS = {
    "customer_name",
    "business_name",
    "balance",
    "days_overdue",
}


def validate_message_placeholders(form, field):
    placeholders = re.findall(r"\{([^{}]*)\}", field.data or "")
    unknown = sorted(set(placeholders) - SUPPORTED_PLACEHOLDERS)
    malformed = re.sub(r"\{[^{}]*\}", "", field.data or "")
    if unknown:
        raise ValidationError(
            "Unsupported placeholder(s): "
            + ", ".join(f"{{{placeholder}}}" for placeholder in unknown)
            + ". Use only the listed placeholders."
        )
    if "{" in malformed or "}" in malformed:
        raise ValidationError("Placeholders must use complete braces, for example {customer_name}.")


class ReminderActionForm(FlaskForm):
    """CSRF protection for reminder actions that do not have input fields."""


class ReminderRuleForm(FlaskForm):
    trigger_days_overdue = IntegerField(
        "Send after the credit sale (days)",
        validators=[InputRequired(), NumberRange(min=0, max=365)],
    )
    channel = SelectField(
        "Channel",
        choices=[("sms", "SMS"), ("whatsapp", "WhatsApp"), ("both", "SMS + WhatsApp")],
        default="sms",
    )
    message_template = TextAreaField(
        "Message Template",
        validators=[
            DataRequired(),
            Length(max=500),
            validate_message_placeholders,
        ],
        default=(
            "Dear {customer_name}, you have an outstanding balance of {balance} with "
            "{business_name}, overdue by {days_overdue} days. Kindly settle at your "
            "earliest convenience. Thank you."
        ),
    )
    active = BooleanField("Active", default=True)
    submit = SubmitField("Save Rule")
