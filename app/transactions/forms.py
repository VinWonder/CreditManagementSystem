from decimal import Decimal, InvalidOperation

from flask_wtf import FlaskForm
from wtforms import StringField, DecimalField, SelectField, SubmitField
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


class TransactionForm(FlaskForm):
    customer_id = SelectField(
        "Customer",
        coerce=_coerce_customer_id,
        choices=[("", "Choose a customer")],
        validators=[DataRequired()],
    )
    item_description = StringField(
        "Item / Goods Description",
        filters=[lambda value: value.strip() if value else value],
        validators=[DataRequired(), Length(max=150)],
    )
    amount = DecimalField(
        "Amount (KES)",
        places=2,
        validators=[DataRequired(), _validate_amount],
    )
    submit = SubmitField("Record Credit Sale")
