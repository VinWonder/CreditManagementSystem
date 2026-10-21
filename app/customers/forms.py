from flask_wtf import FlaskForm
from wtforms import BooleanField, StringField, SelectField, SubmitField
from wtforms.validators import DataRequired, Length, Optional, Regexp


class CustomerForm(FlaskForm):
    full_name = StringField("Customer Full Name", validators=[DataRequired(), Length(max=100)])
    phone_number = StringField(
        "Phone Number",
        validators=[
            DataRequired(),
            Regexp(r"^\+?\d{9,15}$", message="Enter a valid phone number, e.g. +254712345678"),
        ],
    )
    shop_name = StringField(
        "Customer's Shop / Business (optional)",
        validators=[Optional(), Length(max=100)],
    )
    preferred_channel = SelectField(
        "Preferred Reminder Channel",
        choices=[("sms", "SMS"), ("whatsapp", "WhatsApp"), ("both", "SMS + WhatsApp")],
        default="sms",
    )
    accepts_stk_prompts = BooleanField(
        "Customer agrees to receive M-Pesa payment prompts on this number"
    )
    submit = SubmitField("Save Customer")
