import re

from flask_wtf import FlaskForm
from flask_wtf.file import FileField
from wtforms import StringField, PasswordField, SubmitField
from wtforms.validators import DataRequired, Email, Length, EqualTo, Regexp, ValidationError


PASSWORD_MIN_LENGTH = 8
PASSWORD_RULES = [
    (r"[A-Z]", "an uppercase letter"),
    (r"[a-z]", "a lowercase letter"),
    (r"\d", "a number"),
    (r"[^A-Za-z0-9\s]", "a symbol such as ! @ # or $"),
]


def strong_password(form, field):
    """Require every character class and reject spaces, naming each missing rule."""
    value = field.data or ""
    if re.search(r"\s", value):
        raise ValidationError("Passwords cannot contain spaces.")
    missing = [label for pattern, label in PASSWORD_RULES if not re.search(pattern, value)]
    if missing:
        raise ValidationError("Add " + ", ".join(missing) + ".")


PASSWORD_VALIDATORS = [
    DataRequired(),
    Length(
        min=PASSWORD_MIN_LENGTH,
        max=128,
        message=f"Use between {PASSWORD_MIN_LENGTH} and 128 characters.",
    ),
    strong_password,
]


class RegisterForm(FlaskForm):
    business_name = StringField("Business / Shop Name", validators=[DataRequired(), Length(max=100)])
    owner_name = StringField("Owner Full Name", validators=[DataRequired(), Length(max=100)])
    email = StringField("Business Email", validators=[DataRequired(), Email(), Length(max=255)])
    phone_number = StringField(
        "Phone Number",
        validators=[
            DataRequired(),
            Regexp(
                r"^\+?[\d\s().-]{7,25}$",
                message="Enter a valid phone number using the country selector.",
            ),
        ],
    )
    password = PasswordField("Password", validators=PASSWORD_VALIDATORS)
    confirm_password = PasswordField(
        "Confirm Password", validators=[DataRequired(), EqualTo("password")]
    )
    logo = FileField("Shop Logo")
    submit = SubmitField("Create Account")


class LoginForm(FlaskForm):
    phone_number = StringField(
        "Phone Number",
        validators=[DataRequired(), Regexp(r"^\+?[\d\s().-]{7,25}$")],
    )
    password = PasswordField("Password", validators=[DataRequired()])
    submit = SubmitField("Log In")


class LogoutForm(FlaskForm):
    """CSRF-protected form for ending an authenticated session."""


class ShopkeeperForm(FlaskForm):
    full_name = StringField("Shopkeeper full name", validators=[DataRequired(), Length(max=100)])
    phone_number = StringField(
        "Phone Number",
        validators=[
            DataRequired(),
            Regexp(
                r"^\+?[\d\s().-]{7,25}$",
                message="Enter a valid phone number using the country selector.",
            ),
        ],
    )
    password = PasswordField("Initial password", validators=PASSWORD_VALIDATORS)
    submit = SubmitField("Create shopkeeper account")


class ShopkeeperActionForm(FlaskForm):
    submit = SubmitField("Update shopkeeper access")


class ForgotPasswordForm(FlaskForm):
    account = StringField(
        "Phone number or email",
        validators=[DataRequired(message="Enter your phone number or email."), Length(max=255)],
    )
    submit = SubmitField("Email me a code")


class VerifyResetCodeForm(FlaskForm):
    code = StringField(
        "Verification code",
        validators=[DataRequired(), Length(min=6, max=6), Regexp(r"^\d{6}$")],
    )
    submit = SubmitField("Verify code")


class VerifyEmailForm(FlaskForm):
    code = StringField(
        "Verification code",
        validators=[DataRequired(), Length(min=6, max=6), Regexp(r"^\d{6}$", message="Enter the 6-digit code.")],
    )
    submit = SubmitField("Verify email")


class ResetPasswordForm(FlaskForm):
    password = PasswordField("New password", validators=PASSWORD_VALIDATORS)
    confirm_password = PasswordField(
        "Confirm new password", validators=[DataRequired(), EqualTo("password")]
    )
    submit = SubmitField("Update password")


class ResendVerificationForm(FlaskForm):
    submit = SubmitField("Send a new code")
