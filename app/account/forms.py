from flask_wtf import FlaskForm
from flask_wtf.file import FileField
from wtforms import BooleanField, PasswordField, StringField, SubmitField
from wtforms.validators import DataRequired, Email, EqualTo, Length, Regexp

from app.auth.forms import PASSWORD_VALIDATORS

PHONE = Regexp(r"^\+?[\d\s().-]{7,25}$", message="Enter a valid phone number using the country selector.")


class ProfileForm(FlaskForm):
    business_name = StringField("Business / Shop Name", validators=[DataRequired(), Length(max=100)])
    owner_name = StringField("Owner Full Name", validators=[DataRequired(), Length(max=100)])
    phone_number = StringField("Phone Number", validators=[DataRequired(), PHONE])
    logo = FileField("Shop Logo")
    remove_logo = BooleanField("Remove the current logo")
    current_password = PasswordField("Current password (needed to change the phone number)")
    submit = SubmitField("Save profile")


class ChangePasswordForm(FlaskForm):
    current_password = PasswordField("Current password", validators=[DataRequired()])
    password = PasswordField("New password", validators=PASSWORD_VALIDATORS)
    confirm_password = PasswordField(
        "Confirm new password", validators=[DataRequired(), EqualTo("password", message="The passwords do not match.")]
    )
    submit = SubmitField("Change password")


class ChangeEmailForm(FlaskForm):
    email = StringField("New email", validators=[DataRequired(), Email(), Length(max=255)])
    current_password = PasswordField("Current password", validators=[DataRequired()])
    submit = SubmitField("Send a code to the new email")


class ConfirmEmailForm(FlaskForm):
    code = StringField(
        "Verification code",
        validators=[DataRequired(), Length(min=6, max=6), Regexp(r"^\d{6}$", message="Enter the 6-digit code.")],
    )
    submit = SubmitField("Confirm new email")


class ResendCodeForm(FlaskForm):
    submit = SubmitField("Send a new code")
