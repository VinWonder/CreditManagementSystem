from flask_wtf import FlaskForm
from wtforms import IntegerField, PasswordField, SelectField, StringField, SubmitField
from wtforms.validators import DataRequired, Length, NumberRange, Optional, Regexp, ValidationError

SHORTCODE = Regexp(r"^\d{5,7}$", message="Use the 5-7 digit number from Safaricom.")


class MpesaSettingsForm(FlaskForm):
    environment = SelectField(
        "Mode",
        choices=[
            ("sandbox", "Sandbox (testing, no real money)"),
            ("production", "Live (real payments)"),
        ],
    )
    account_type = SelectField(
        "Customers pay to",
        choices=[("paybill", "Paybill number"), ("till", "Till number (Buy Goods)")],
    )
    business_shortcode = StringField(
        "Paybill number, or store number for a Till",
        validators=[DataRequired(), SHORTCODE],
    )
    till_number = StringField("Till number", validators=[Optional(), SHORTCODE])
    # Secrets are never shown again; leaving them blank keeps the saved value.
    consumer_key = PasswordField("Consumer key", validators=[Length(max=200)])
    consumer_secret = PasswordField("Consumer secret", validators=[Length(max=200)])
    passkey = PasswordField("Lipa Na M-Pesa passkey", validators=[Length(max=200)])
    submit = SubmitField("Save M-Pesa settings")

    def __init__(self, *args, has_saved_secrets=False, **kwargs):
        super().__init__(*args, **kwargs)
        self.has_saved_secrets = has_saved_secrets

    def validate_till_number(self, field):
        if self.account_type.data == "till" and not field.data:
            raise ValidationError("Enter the Till number customers pay to.")

    def _require_secret(self, field):
        if not self.has_saved_secrets and not (field.data or "").strip():
            raise ValidationError("Required the first time you save.")

    def validate_consumer_key(self, field):
        self._require_secret(field)

    def validate_consumer_secret(self, field):
        self._require_secret(field)

    def validate_passkey(self, field):
        self._require_secret(field)


class StkPushForm(FlaskForm):
    amount = IntegerField(
        "Amount to request (KES)",
        validators=[
            DataRequired(message="Enter a whole-shilling amount."),
            NumberRange(min=1, max=250000, message="M-Pesa prompts take KES 1 to 250,000."),
        ],
    )
    phone_number = StringField(
        "Customer's M-Pesa number",
        validators=[DataRequired(), Length(max=20)],
    )
    submit = SubmitField("Send M-Pesa prompt")


class StkActionForm(FlaskForm):
    submit = SubmitField("Check status")
