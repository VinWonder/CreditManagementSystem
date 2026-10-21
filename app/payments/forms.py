import re
from decimal import Decimal, InvalidOperation

from flask_wtf import FlaskForm
from wtforms import SelectField, DecimalField, SubmitField, StringField
from wtforms.validators import DataRequired, Length, ValidationError

from app.models import PAYMENT_METHODS

# Methods that leave an M-Pesa confirmation code the admin can check.
MPESA_CODE_METHODS = {"agent", "mpesa_send", "mpesa_till", "mpesa_paybill"}


def _coerce_customer_id(value):
    return int(value) if value else None


def _validate_amount(form, field):
    if field.data is None:
        return

    try:
        amount = Decimal(field.raw_data[0].strip())
    except (InvalidOperation, IndexError):
        return

    if not amount.is_finite():
        raise ValidationError("Enter a valid amount.")
    if amount.normalize().as_tuple().exponent < -2:
        raise ValidationError("Use no more than 2 decimal places.")
    if amount < Decimal("0.01"):
        raise ValidationError("Amount must be at least KES 0.01.")
    if amount > Decimal("99999999.99"):
        raise ValidationError("Amount must be KES 99,999,999.99 or less.")


def _validate_amount_change(form, field):
    if field.data is None:
        return

    try:
        amount = Decimal(field.raw_data[0].strip())
    except (InvalidOperation, IndexError):
        return

    if not amount.is_finite() or amount == 0:
        raise ValidationError("Enter a non-zero balance change.")
    if amount.normalize().as_tuple().exponent < -2:
        raise ValidationError("Use no more than 2 decimal places.")
    if abs(amount) > Decimal("99999999.99"):
        raise ValidationError("The change must be KES 99,999,999.99 or less.")


class PaymentForm(FlaskForm):
    customer_id = SelectField(
        "Customer",
        coerce=_coerce_customer_id,
        choices=[("", "Choose a customer")],
        validators=[DataRequired()],
    )
    amount_paid = DecimalField(
        "Amount Paid (KES)",
        places=2,
        validators=[DataRequired(), _validate_amount],
    )
    payment_method = SelectField(
        "Payment method",
        choices=[("", "Choose how the customer paid")] + list(PAYMENT_METHODS.items()),
        validators=[DataRequired(message="Choose how the customer paid.")],
    )
    payment_reference = StringField("Reference", validators=[Length(max=40)])
    submit = SubmitField("Log Payment")

    def validate_payment_reference(self, field):
        method = self.payment_method.data
        reference = re.sub(r"\s+", "", field.data or "").upper()
        field.data = reference or None
        if method in MPESA_CODE_METHODS:
            if not reference:
                raise ValidationError("Enter the M-Pesa transaction code from the confirmation SMS.")
            if not re.fullmatch(r"[A-Z0-9]{8,12}", reference):
                raise ValidationError("M-Pesa codes are 8-12 letters and numbers, e.g. QFT4ABC123.")
        elif method == "cheque":
            if not reference:
                raise ValidationError("Enter the cheque number.")
            if not re.fullmatch(r"[A-Z0-9-]{3,20}", reference):
                raise ValidationError("Cheque numbers use letters, numbers and dashes only.")
        elif method == "bank":
            if reference and not re.fullmatch(r"[A-Z0-9/-]{3,40}", reference):
                raise ValidationError("Bank references use letters, numbers, / and - only.")
        else:
            # Cash leaves no reference to record.
            field.data = None


class PaymentDecisionForm(FlaskForm):
    review_note = StringField("Reason", validators=[Length(max=255)])
    submit = SubmitField("Confirm")


class BalanceAdjustmentForm(FlaskForm):
    amount_change = DecimalField(
        "Balance change (KES)",
        places=2,
        validators=[DataRequired(), _validate_amount_change],
    )
    reason = StringField("Reason for adjustment", validators=[DataRequired(), Length(max=255)])
    submit = SubmitField("Save balance correction")

    def validate_reason(self, field):
        if not field.data.strip():
            raise ValidationError("Enter a reason for the balance correction.")
