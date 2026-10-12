from flask_wtf import FlaskForm
from wtforms import StringField, DecimalField, SelectField, SubmitField
from wtforms.validators import DataRequired, Length, NumberRange


class TransactionForm(FlaskForm):
    customer_id = SelectField("Customer", coerce=int, validators=[DataRequired()])
    item_description = StringField(
        "Item / Goods Description", validators=[DataRequired(), Length(max=150)]
    )
    amount = DecimalField(
        "Amount (KES)", places=2, validators=[DataRequired(), NumberRange(min=1)]
    )
    submit = SubmitField("Record Credit Sale")
