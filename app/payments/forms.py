from flask_wtf import FlaskForm
from wtforms import SelectField, DecimalField, SubmitField
from wtforms.validators import DataRequired, NumberRange


class PaymentForm(FlaskForm):
    customer_id = SelectField("Customer", coerce=int, validators=[DataRequired()])
    amount_paid = DecimalField(
        "Amount Paid (KES)", places=2, validators=[DataRequired(), NumberRange(min=1)]
    )
    submit = SubmitField("Log Payment")
