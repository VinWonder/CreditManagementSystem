import re

from flask_wtf import FlaskForm
from wtforms import (
    BooleanField,
    IntegerField,
    PasswordField,
    SelectField,
    StringField,
    SubmitField,
    TextAreaField,
)
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
    "customer_shop",
    "item_details",
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
            "{business_name}, overdue by {days_overdue} days. Credit purchases: "
            "{item_details}. Kindly settle at your earliest convenience. Thank you."
        ),
    )
    active = BooleanField("Active", default=True)
    submit = SubmitField("Save Rule")


class MessagingSettingsForm(FlaskForm):
    sms_provider = SelectField(
        "SMS provider",
        choices=[
            ("africastalking", "Africa's Talking"),
            ("twilio", "Twilio"),
        ],
        validators=[DataRequired()],
    )
    at_username = StringField("Africa's Talking username", validators=[Length(max=100)])
    at_api_key = PasswordField("Africa's Talking API key", validators=[Length(max=255)])
    clear_at_api_key = BooleanField("Remove the saved Africa's Talking API key")
    twilio_account_sid = StringField("Twilio Account SID", validators=[Length(max=100)])
    twilio_auth_token = PasswordField("Twilio auth token", validators=[Length(max=255)])
    clear_twilio_auth_token = BooleanField("Remove the saved Twilio auth token")
    twilio_from_number = StringField("Twilio sender phone number", validators=[Length(max=30)])
    twilio_messaging_service_sid = StringField(
        "Twilio Messaging Service SID",
        validators=[Length(max=100)],
    )
    whatsapp_phone_id = StringField("WhatsApp Phone Number ID", validators=[Length(max=100)])
    whatsapp_token = PasswordField("WhatsApp access token", validators=[Length(max=4096)])
    clear_whatsapp_token = BooleanField("Remove the saved WhatsApp access token")
    submit = SubmitField("Save provider settings")
