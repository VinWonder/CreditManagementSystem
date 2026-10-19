from decimal import Decimal, InvalidOperation

from flask_wtf import FlaskForm
from wtforms import SelectField, DecimalField, SubmitField, StringField
from wtforms.validators import DataRequired, Length, ValidationError


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
    submit = SubmitField("Log Payment")


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
