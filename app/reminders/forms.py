from flask_wtf import FlaskForm
from wtforms import IntegerField, TextAreaField, SelectField, BooleanField, SubmitField
from wtforms.validators import DataRequired, NumberRange, Length


class ReminderRuleForm(FlaskForm):
    trigger_days_overdue = IntegerField(
        "Send when overdue by (days)", validators=[DataRequired(), NumberRange(min=1, max=365)]
    )
    channel = SelectField(
        "Channel",
        choices=[("sms", "SMS"), ("whatsapp", "WhatsApp"), ("both", "SMS + WhatsApp")],
        default="sms",
    )
    message_template = TextAreaField(
        "Message Template",
        validators=[DataRequired(), Length(max=500)],
        default=(
            "Dear {customer_name}, you have an outstanding balance of {balance} with "
            "{business_name}, overdue by {days_overdue} days. Kindly settle at your "
            "earliest convenience. Thank you."
        ),
    )
    active = BooleanField("Active", default=True)
    submit = SubmitField("Save Rule")
