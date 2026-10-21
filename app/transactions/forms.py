from decimal import Decimal, InvalidOperation

from flask_wtf import FlaskForm
from wtforms import DecimalField, FieldList, Form, FormField, SelectField, StringField, SubmitField
from wtforms.validators import DataRequired, Length, Optional, ValidationError

MAX_SALE_ITEMS = 30


ITEM_SIZE_CHOICES = [
    ("1/2", '1/2"'),
    ("1/4", '1/4"'),
    ("3/4", '3/4"'),
    ("1", '1"'),
    ("1 1/4", '1 1/4"'),
    ("1 1/2", '1 1/2"'),
    ("2", '2"'),
    ("2 1/2", '2 1/2"'),
    ("3", '3"'),
    ("4", '4"'),
    ("6", '6"'),
    ("8", '8"'),
    ("12", '12"'),
    ("16", '16"'),
    ("18", '18"'),
]
ITEM_SIZES_INCHES = {value for value, _label in ITEM_SIZE_CHOICES}


def format_item_description(description, size_inches=""):
    description = description.strip()
    if size_inches:
        description = f"{description} ({size_inches} in)"
    return description


def _validate_item_size_description(form, field):
    description = format_item_description(
        form.item_description.data or "",
        field.data or "",
    )
    if len(description) > 150:
        raise ValidationError("Item description and size must be 150 characters or fewer.")


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


class SaleItemForm(Form):
    """One product in a credit sale."""

    item_description = StringField(
        "Item",
        filters=[lambda value: value.strip() if value else value],
        validators=[DataRequired(message="Enter what the customer took."), Length(max=150)],
    )
    item_size_inches = SelectField(
        "Size (inches)",
        choices=[("", "No size")] + ITEM_SIZE_CHOICES,
        validators=[Optional(), _validate_item_size_description],
    )
    amount = DecimalField(
        "Amount (KES)",
        places=2,
        validators=[DataRequired(message="Enter the item's price."), _validate_amount],
    )

    @property
    def description(self):
        return format_item_description(self.item_description.data or "", self.item_size_inches.data or "")


class TransactionForm(FlaskForm):
    customer_id = SelectField(
        "Customer",
        coerce=_coerce_customer_id,
        choices=[("", "Choose a customer")],
        validators=[DataRequired()],
    )
    # Several products bought at the same time are one sale with one row each.
    items = FieldList(FormField(SaleItemForm), min_entries=1, max_entries=MAX_SALE_ITEMS)
    submit = SubmitField("Record Credit Sale")

    def validate_items(self, field):
        total = sum((entry.form.amount.data or Decimal("0")) for entry in field.entries)
        if total > Decimal("99999999.99"):
            raise ValidationError("The sale total must be KES 99,999,999.99 or less.")

    @property
    def sale_items(self):
        """(description, amount) for each row."""
        return [(entry.form.description, entry.form.amount.data) for entry in self.items.entries]
