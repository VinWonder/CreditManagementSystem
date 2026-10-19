from flask_wtf import FlaskForm
from flask_wtf.file import FileField
from wtforms import StringField, PasswordField, SubmitField
from wtforms.validators import DataRequired, Email, Length, EqualTo, Regexp


PASSWORD_VALIDATORS = [
    DataRequired(),
    Length(min=8, max=128),
    Regexp(r".*[A-Za-z].*", message="Use at least one letter."),
    Regexp(r".*\d.*", message="Use at least one number."),
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
    phone_number = StringField(
        "Phone Number",
        validators=[DataRequired(), Regexp(r"^\+?[\d\s().-]{7,25}$")],
    )
    submit = SubmitField("Send verification code")


class VerifyResetCodeForm(FlaskForm):
    code = StringField(
        "Verification code",
        validators=[DataRequired(), Length(min=6, max=6), Regexp(r"^\d{6}$")],
    )
    submit = SubmitField("Verify code")


class ResetPasswordForm(FlaskForm):
    password = PasswordField("New password", validators=PASSWORD_VALIDATORS)
    confirm_password = PasswordField(
        "Confirm new password", validators=[DataRequired(), EqualTo("password")]
    )
    submit = SubmitField("Update password")


class ResendVerificationForm(FlaskForm):
    submit = SubmitField("Send a new code")
