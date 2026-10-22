from datetime import datetime, timedelta
import os
import re
import secrets
from urllib.parse import urljoin, urlparse

from flask import Blueprint, current_app, render_template, redirect, url_for, flash, request, session
from flask_login import login_user, logout_user, login_required, current_user
from sqlalchemy.exc import IntegrityError
from werkzeug.security import generate_password_hash, check_password_hash
from werkzeug.utils import secure_filename
import phonenumbers
from phonenumbers import NumberParseException, PhoneNumberFormat

from app.extensions import db
from app.models import (
    Retailer,
    Shopkeeper,
    PasswordResetCode,
    PhoneVerificationCode,
    LoginChallenge,
    EmailVerificationCode,
)
from app.auth.forms import (
    RegisterForm,
    LoginForm,
    ShopkeeperForm,
    ShopkeeperActionForm,
    ForgotPasswordForm,
    VerifyResetCodeForm,
    ResetPasswordForm,
    ResendVerificationForm,
    VerifyEmailForm,
)
from app.auth.email import send_email
from app.auth.identity import canonical_email, clean_shop_name, shop_name_key
from app.auth.signups import release_abandoned_signups
from app.reminders.dispatch_service import send_sms
from app.permissions import admin_required

auth_bp = Blueprint("auth", __name__, url_prefix="/auth")


def normalize_phone(phone_number):
    try:
        parsed = phonenumbers.parse(
            phone_number.strip(), current_app.config["DEFAULT_PHONE_REGION"]
        )
    except NumberParseException as exc:
        raise ValueError("Enter a valid phone number with its country code.") from exc
    if not phonenumbers.is_valid_number(parsed):
        raise ValueError("Enter a valid phone number with its country code.")
    return phonenumbers.format_number(parsed, PhoneNumberFormat.E164)


def is_safe_redirect(target):
    if not target:
        return False
    host_url = urlparse(request.host_url)
    redirect_url = urlparse(urljoin(request.host_url, target))
    return redirect_url.scheme in {"http", "https"} and redirect_url.netloc == host_url.netloc


def normalized_email(email):
    return email.strip().lower()


def send_verification_sms(phone_number, message):
    success, info = send_sms(phone_number, message)
    if success and info.startswith("simulated") and current_app.config["APP_ENV"] == "production":
        return False, "SMS provider is not configured for production"
    return success, info


def save_shop_logo(file_storage):
    if not file_storage or not file_storage.filename:
        return None
    original_name = secure_filename(file_storage.filename)
    extension = os.path.splitext(original_name)[1].lower().lstrip(".")
    if extension not in {"jpg", "jpeg", "png", "webp"}:
        raise ValueError("Upload a JPG, PNG, or WEBP image.")
    if not (file_storage.mimetype or "").startswith("image/"):
        raise ValueError("The shop logo must be an image file.")

    os.makedirs(current_app.config["SHOP_LOGO_UPLOAD_FOLDER"], exist_ok=True)
    filename = f"{secrets.token_hex(16)}.{extension}"
    file_storage.save(os.path.join(current_app.config["SHOP_LOGO_UPLOAD_FOLDER"], filename))
    return filename


def create_phone_verification(retailer):
    PhoneVerificationCode.query.filter_by(
        retailer_id=retailer.retailer_id, used_at=None
    ).delete(synchronize_session=False)
    code = f"{secrets.randbelow(1_000_000):06d}"
    verification = PhoneVerificationCode(
        retailer_id=retailer.retailer_id,
        code_hash=generate_password_hash(code, method="scrypt"),
        expires_at=datetime.utcnow()
        + timedelta(minutes=current_app.config["PHONE_VERIFICATION_CODE_MINUTES"]),
    )
    db.session.add(verification)
    db.session.commit()
    success, info = send_verification_sms(
        retailer.phone_number,
        f"Duka Credit verification code: {code}. It expires in "
        f"{current_app.config['PHONE_VERIFICATION_CODE_MINUTES']} minutes.",
    )
    if success and info.startswith("simulated"):
        current_app.logger.info(
            "Simulated SMS verification code for retailer %s: %s",
            retailer.retailer_id,
            code,
        )
    return success, info


def create_login_challenge(retailer):
    latest = (
        LoginChallenge.query.filter_by(retailer_id=retailer.retailer_id, used_at=None)
        .order_by(LoginChallenge.created_at.desc())
        .first()
    )
    cooldown = timedelta(seconds=current_app.config["LOGIN_OTP_RESEND_SECONDS"])
    if latest and datetime.utcnow() - latest.sent_at < cooldown:
        return None, False, "A code was sent recently. Please wait before trying again."
    if latest:
        latest.used_at = datetime.utcnow()
        db.session.flush()
    code = f"{secrets.randbelow(1_000_000):06d}"
    challenge = LoginChallenge(
        retailer_id=retailer.retailer_id,
        code_hash=generate_password_hash(code, method="scrypt"),
        expires_at=datetime.utcnow()
        + timedelta(minutes=current_app.config["LOGIN_OTP_CODE_MINUTES"]),
    )
    db.session.add(challenge)
    db.session.flush()
    success, info = send_verification_sms(
        retailer.phone_number,
        f"Your Duka Credit sign-in code is {code}. It expires in "
        f"{current_app.config['LOGIN_OTP_CODE_MINUTES']} minutes. Do not share this code.",
    )
    if not success:
        db.session.rollback()
        current_app.logger.warning("Login verification SMS failed: %s", info)
        return None, False, info
    db.session.commit()
    if info.startswith("simulated"):
        current_app.logger.info(
            "Simulated sign-in code for retailer %s: %s", retailer.retailer_id, code
        )
    return challenge, True, info


def create_email_verification(retailer, to_address=None):
    """Email a fresh one-time code to the retailer. Returns (flash_category, message).

    to_address sends the code to a new address the retailer wants to switch to
    instead of the current one. The category is "danger" only when the email
    could not be sent at all.
    """
    to_address = to_address or retailer.email
    changing = to_address != retailer.email
    latest = (
        EmailVerificationCode.query.filter_by(retailer_id=retailer.retailer_id, used_at=None)
        .order_by(EmailVerificationCode.created_at.desc())
        .first()
    )
    cooldown = timedelta(seconds=current_app.config["EMAIL_VERIFICATION_RESEND_SECONDS"])
    if latest and datetime.utcnow() - latest.sent_at < cooldown:
        return "warning", "A code was sent recently. Please wait a minute before asking for another."
    EmailVerificationCode.query.filter_by(
        retailer_id=retailer.retailer_id, used_at=None
    ).update({"used_at": datetime.utcnow()}, synchronize_session=False)

    minutes = current_app.config["EMAIL_VERIFICATION_CODE_MINUTES"]
    code = f"{secrets.randbelow(1_000_000):06d}"
    db.session.add(
        EmailVerificationCode(
            retailer_id=retailer.retailer_id,
            code_hash=generate_password_hash(code, method="scrypt"),
            expires_at=datetime.utcnow() + timedelta(minutes=minutes),
        )
    )
    db.session.flush()
    reason = (
        "confirm this as the new email for your Duka Credit shop"
        if changing
        else "finish setting up your Duka Credit account"
    )
    success, info = send_email(
        to_address,
        "Your Duka Credit verification code",
        f"Hello {retailer.owner_name},\n\n"
        f"Your Duka Credit verification code is {code}. Use it to {reason}. "
        f"It expires in {minutes} minutes.\n\n"
        "If you did not ask for this, you can ignore this email.\n",
    )
    if not success:
        db.session.rollback()
        current_app.logger.warning("Verification email failed: %s", info)
        return "danger", "We could not send the verification email. Please try again shortly."
    db.session.commit()
    if info == "simulated":
        # No SMTP server is configured, so nothing left this machine.
        current_app.logger.warning(
            "Email is not configured (SMTP_HOST is empty), so no email was sent. "
            "Verification code for retailer %s: %s",
            retailer.retailer_id,
            code,
        )
        return (
            "warning",
            "Email sending is not set up on this server yet, so no email was sent. "
            "For testing, the code is printed in the server terminal.",
        )
    return "info", f"We sent a verification code to {to_address}."


def registration_conflicts(business_name, phone_number, email):
    """Field name -> message for each detail that already belongs to an account."""
    problems = {}
    if Retailer.query.filter(
        db.func.lower(Retailer.business_name) == shop_name_key(business_name)
    ).first():
        problems["business_name"] = (
            "A shop with this name is already registered. If it is yours, log in; "
            "otherwise add your area or branch, e.g. 'Mama Njeri Shop Kitengela'."
        )
    if (
        Retailer.query.filter_by(phone_number=phone_number).first()
        or Shopkeeper.query.filter_by(phone_number=phone_number).first()
    ):
        problems["phone_number"] = (
            "This phone number is already registered. Log in, or use Forgot password."
        )
    if Retailer.query.filter(
        (Retailer.email == email) | (Retailer.email_canonical == canonical_email(email))
    ).first():
        problems["email"] = (
            "This email is already registered to a shop. Log in, or use Forgot password."
        )
    return problems


def password_guessable_from(password, phone_number, email):
    """A reason the password is easy to guess from the account's own details, or None."""
    lowered = (password or "").lower()
    digits = re.sub(r"\D", "", password or "")
    if phone_number and phone_number[-9:] in digits:
        return "Don't use your phone number in your password."
    mailbox = canonical_email(email).split("@")[0]
    if len(mailbox) >= 4 and mailbox in lowered.replace(".", ""):
        return "Don't use your email name in your password."
    return None


def find_retailer_for_reset(account):
    """Match the forgot-password box to a retailer by email or phone number."""
    account = (account or "").strip()
    if "@" in account:
        return Retailer.query.filter_by(email=normalized_email(account)).first()
    try:
        return Retailer.query.filter_by(phone_number=normalize_phone(account)).first()
    except ValueError:
        return None


def send_password_reset_code(retailer):
    """Email a fresh reset code, or text it when the account has no email.

    Returns True when a code was sent. A code sent in the last minute is not
    replaced, so the form cannot be used to flood someone's inbox.
    """
    latest = (
        PasswordResetCode.query.filter_by(retailer_id=retailer.retailer_id, used_at=None)
        .order_by(PasswordResetCode.created_at.desc())
        .first()
    )
    cooldown = timedelta(seconds=current_app.config["PASSWORD_RESET_RESEND_SECONDS"])
    if latest and datetime.utcnow() - latest.created_at < cooldown:
        return False

    PasswordResetCode.query.filter_by(
        retailer_id=retailer.retailer_id, used_at=None
    ).delete(synchronize_session=False)
    minutes = current_app.config["PASSWORD_RESET_CODE_MINUTES"]
    code = f"{secrets.randbelow(1_000_000):06d}"
    db.session.add(
        PasswordResetCode(
            retailer_id=retailer.retailer_id,
            code_hash=generate_password_hash(code, method="scrypt"),
            expires_at=datetime.utcnow() + timedelta(minutes=minutes),
        )
    )
    db.session.flush()

    if retailer.email:
        success, info = send_email(
            retailer.email,
            "Your Duka Credit password reset code",
            f"Hello {retailer.owner_name},\n\n"
            f"Your Duka Credit password reset code is {code}. It expires in {minutes} minutes.\n\n"
            "If you did not ask to reset your password, ignore this email; your password "
            "stays the same.\n",
        )
        channel = "email"
    else:
        # Accounts created before email sign-up only have a phone number.
        success, info = send_verification_sms(
            retailer.phone_number,
            f"Duka Credit password reset code: {code}. It expires in {minutes} minutes.",
        )
        channel = "SMS"

    if not success:
        db.session.rollback()
        current_app.logger.warning("Password reset %s failed: %s", channel, info)
        return False
    db.session.commit()
    if info.startswith("simulated"):
        current_app.logger.warning(
            "%s is not configured, so no %s was sent. Password reset code for retailer %s: %s",
            "Email" if channel == "email" else "SMS",
            channel,
            retailer.retailer_id,
            code,
        )
    return True


def start_email_verification(retailer):
    """Remember who is verifying, send a code and show the code entry page."""
    session["email_verification_retailer_id"] = retailer.retailer_id
    category, message = create_email_verification(retailer)
    flash(message, category)
    return redirect(url_for("auth.verify_email"))


@auth_bp.route("/register", methods=["GET", "POST"])
def register():
    if current_user.is_authenticated:
        return redirect(url_for("main.dashboard"))

    form = RegisterForm()
    if form.validate_on_submit():
        business_name = clean_shop_name(form.business_name.data)
        owner_name = clean_shop_name(form.owner_name.data)
        email = normalized_email(form.email.data)
        try:
            phone_number = normalize_phone(form.phone_number.data)
        except ValueError as exc:
            form.phone_number.errors.append(str(exc))
            return render_template("auth/register.html", form=form)

        # Details held by a sign-up that never verified its email are freed first.
        release_abandoned_signups(business_name, phone_number, email)
        problems = registration_conflicts(business_name, phone_number, email)
        guessable = password_guessable_from(form.password.data, phone_number, email)
        if guessable:
            problems["password"] = guessable
        if problems:
            for field_name, message in problems.items():
                getattr(form, field_name).errors.append(message)
            return render_template("auth/register.html", form=form)

        try:
            logo_filename = save_shop_logo(form.logo.data)
        except ValueError as exc:
            form.logo.errors.append(str(exc))
            return render_template("auth/register.html", form=form)

        retailer = Retailer(
            business_name=business_name,
            owner_name=owner_name,
            phone_number=phone_number,
            email=email,
            email_canonical=canonical_email(email),
            logo_filename=logo_filename,
        )
        retailer.set_password(form.password.data)
        db.session.add(retailer)
        try:
            db.session.commit()
        except IntegrityError:
            # Someone registered the same details a moment ago; the database
            # constraints caught it. Say which field clashed.
            db.session.rollback()
            if logo_filename:
                try:
                    os.remove(os.path.join(current_app.config["SHOP_LOGO_UPLOAD_FOLDER"], logo_filename))
                except OSError:
                    pass
            problems = registration_conflicts(business_name, phone_number, email) or {
                "business_name": "These details were just registered. Please check them and try again."
            }
            for field_name, message in problems.items():
                getattr(form, field_name).errors.append(message)
            return render_template("auth/register.html", form=form)

        session["email_verification_retailer_id"] = retailer.retailer_id
        category, message = create_email_verification(retailer)
        if category == "danger":
            # Without the code the account could never be used; don't let it
            # block a second try with the same name, phone and email.
            session.pop("email_verification_retailer_id", None)
            db.session.delete(retailer)
            db.session.commit()
            if logo_filename:
                try:
                    os.remove(os.path.join(current_app.config["SHOP_LOGO_UPLOAD_FOLDER"], logo_filename))
                except OSError:
                    pass
            form.email.errors.append(
                f"We couldn't send the verification email to {email}, so your account was not "
                "created. Check the address and try again in a minute."
            )
            return render_template("auth/register.html", form=form)
        flash(message, category)
        return redirect(url_for("auth.verify_email"))

    return render_template("auth/register.html", form=form)


@auth_bp.route("/login", methods=["GET", "POST"])
def login():
    if current_user.is_authenticated:
        return redirect(url_for("main.dashboard"))

    form = LoginForm()
    if form.validate_on_submit():
        try:
            phone_number = normalize_phone(form.phone_number.data)
        except ValueError:
            phone_number = None
        retailer = Retailer.query.filter_by(phone_number=phone_number).first()
        shopkeeper = (
            Shopkeeper.query.filter_by(phone_number=phone_number, active=True).first()
            if not retailer
            else None
        )
        account = retailer or shopkeeper
        if account and account.check_password(form.password.data):
            if retailer and retailer.needs_email_verification:
                return start_email_verification(retailer)
            session.pop("phone_verification_retailer_id", None)
            session.pop("email_verification_retailer_id", None)
            session.pop("login_challenge_id", None)
            session.pop("login_next", None)
            next_page = request.args.get("next")
            session.permanent = True
            login_user(account, fresh=True)
            destination = next_page if is_safe_redirect(next_page) else url_for("main.dashboard")
            return redirect(destination)

        flash("Invalid phone number or password.", "danger")

    return render_template("auth/login.html", form=form)


@auth_bp.route("/shopkeepers", methods=["GET", "POST"])
@login_required
@admin_required
def manage_shopkeepers():
    form = ShopkeeperForm()
    if form.validate_on_submit():
        try:
            phone_number = normalize_phone(form.phone_number.data)
        except ValueError as exc:
            form.phone_number.errors.append(str(exc))
            return render_template(
                "auth/shopkeepers.html",
                form=form,
                action_form=ShopkeeperActionForm(),
                shopkeepers=current_user.shopkeepers.order_by(Shopkeeper.full_name).all(),
            )

        if Retailer.query.filter_by(phone_number=phone_number).first() or Shopkeeper.query.filter_by(
            phone_number=phone_number
        ).first():
            form.phone_number.errors.append("That phone number is already in use.")
        else:
            shopkeeper = Shopkeeper(
                retailer_id=current_user.retailer_id,
                full_name=form.full_name.data.strip(),
                phone_number=phone_number,
            )
            shopkeeper.set_password(form.password.data)
            db.session.add(shopkeeper)
            try:
                db.session.commit()
            except IntegrityError:
                db.session.rollback()
                form.phone_number.errors.append("That phone number is already in use.")
                return render_template(
                    "auth/shopkeepers.html",
                    form=form,
                    action_form=ShopkeeperActionForm(),
                    shopkeepers=current_user.shopkeepers.order_by(Shopkeeper.full_name).all(),
                )
            flash(f"Shopkeeper account created for {shopkeeper.full_name}.", "success")
            return redirect(url_for("auth.manage_shopkeepers"))

    return render_template(
        "auth/shopkeepers.html",
        form=form,
        action_form=ShopkeeperActionForm(),
        shopkeepers=current_user.shopkeepers.order_by(Shopkeeper.full_name).all(),
    )


@auth_bp.route("/shopkeepers/<int:shopkeeper_id>/toggle", methods=["POST"])
@login_required
@admin_required
def toggle_shopkeeper(shopkeeper_id):
    form = ShopkeeperActionForm()
    if not form.validate_on_submit():
        flash("The account change could not be verified. Please try again.", "danger")
        return redirect(url_for("auth.manage_shopkeepers"))

    shopkeeper = Shopkeeper.query.filter_by(
        shopkeeper_id=shopkeeper_id,
        retailer_id=current_user.retailer_id,
    ).first_or_404()
    shopkeeper.active = not shopkeeper.active
    db.session.commit()
    flash(
        f"{shopkeeper.full_name}'s account "
        f"{'activated' if shopkeeper.active else 'deactivated'}.",
        "success",
    )
    return redirect(url_for("auth.manage_shopkeepers"))


@auth_bp.route("/shopkeepers/<int:shopkeeper_id>/password", methods=["GET", "POST"])
@login_required
@admin_required
def reset_shopkeeper_password(shopkeeper_id):
    shopkeeper = Shopkeeper.query.filter_by(
        shopkeeper_id=shopkeeper_id,
        retailer_id=current_user.retailer_id,
    ).first_or_404()
    form = ResetPasswordForm()
    if form.validate_on_submit():
        shopkeeper.set_password(form.password.data)
        db.session.commit()
        flash(f"Password updated for {shopkeeper.full_name}.", "success")
        return redirect(url_for("auth.manage_shopkeepers"))

    return render_template(
        "auth/reset_shopkeeper_password.html",
        shopkeeper=shopkeeper,
        form=form,
    )


@auth_bp.route("/verify-login", methods=["GET", "POST"])
def verify_login():
    flash("Sign-in by SMS code is temporarily disabled. Please log in with your phone number and password.", "info")
    return redirect(url_for("auth.login"))


@auth_bp.route("/resend-login-code", methods=["POST"])
def resend_login_code():
    flash("Sign-in by SMS code is temporarily disabled. Please log in with your phone number and password.", "info")
    return redirect(url_for("auth.login"))


@auth_bp.route("/verify-phone", methods=["GET", "POST"])
def verify_phone():
    flash("Phone verification is temporarily disabled. Please log in with your phone number and password.", "info")
    return redirect(url_for("auth.login"))


@auth_bp.route("/resend-phone-verification", methods=["POST"])
def resend_phone_verification():
    flash("Phone verification is temporarily disabled. Please log in with your phone number and password.", "info")
    return redirect(url_for("auth.login"))


@auth_bp.route("/verify-email", methods=["GET", "POST"])
def verify_email():
    if current_user.is_authenticated:
        return redirect(url_for("main.dashboard"))

    retailer_id = session.get("email_verification_retailer_id")
    retailer = db.session.get(Retailer, retailer_id) if retailer_id else None
    if not retailer or not retailer.needs_email_verification:
        session.pop("email_verification_retailer_id", None)
        return redirect(url_for("auth.login"))

    form = VerifyEmailForm()
    if form.validate_on_submit():
        now = datetime.utcnow()
        code = (
            EmailVerificationCode.query.filter_by(retailer_id=retailer.retailer_id, used_at=None)
            .order_by(EmailVerificationCode.created_at.desc())
            .first()
        )
        if (
            not code
            or code.expires_at <= now
            or code.attempts >= current_app.config["EMAIL_VERIFICATION_MAX_ATTEMPTS"]
        ):
            flash("That code is invalid or expired. Send a new code and try again.", "danger")
        elif not check_password_hash(code.code_hash, form.code.data):
            code.attempts += 1
            db.session.commit()
            flash("That code is invalid or expired. Send a new code and try again.", "danger")
        else:
            code.used_at = now
            retailer.email_verified_at = now
            db.session.commit()
            session.pop("email_verification_retailer_id", None)
            session.permanent = True
            login_user(retailer, fresh=True)
            flash("Email verified. Welcome to Duka Credit!", "success")
            return redirect(url_for("main.dashboard"))

    return render_template(
        "auth/verify_email.html",
        form=form,
        resend_form=ResendVerificationForm(),
        email=retailer.email,
        code_minutes=current_app.config["EMAIL_VERIFICATION_CODE_MINUTES"],
    )


@auth_bp.route("/resend-email-verification", methods=["POST"])
def resend_email_verification():
    retailer_id = session.get("email_verification_retailer_id")
    retailer = db.session.get(Retailer, retailer_id) if retailer_id else None
    form = ResendVerificationForm()
    if not retailer or not retailer.needs_email_verification or not form.validate_on_submit():
        return redirect(url_for("auth.login"))
    category, message = create_email_verification(retailer)
    flash(message, category)
    return redirect(url_for("auth.verify_email"))


@auth_bp.route("/forgot-password", methods=["GET", "POST"])
def forgot_password():
    if current_user.is_authenticated:
        return redirect(url_for("main.dashboard"))

    form = ForgotPasswordForm()
    if form.validate_on_submit():
        retailer = find_retailer_for_reset(form.account.data)
        if retailer:
            send_password_reset_code(retailer)
        # Remember who asked without revealing whether the account exists.
        session["password_reset_retailer_id"] = retailer.retailer_id if retailer else None
        flash(
            "If an account matches, we have sent a 6-digit code to its email address. "
            "It usually arrives within a minute; check your spam folder too.",
            "info",
        )
        return redirect(url_for("auth.verify_reset_code"))

    return render_template("auth/forgot_password.html", form=form)


@auth_bp.route("/resend-reset-code", methods=["POST"])
def resend_reset_code():
    form = ResendVerificationForm()
    retailer_id = session.get("password_reset_retailer_id")
    retailer = db.session.get(Retailer, retailer_id) if retailer_id else None
    if form.validate_on_submit() and retailer:
        send_password_reset_code(retailer)
    flash("If your account exists, a new code is on its way.", "info")
    return redirect(url_for("auth.verify_reset_code"))


@auth_bp.route("/verify-reset-code", methods=["GET", "POST"])
def verify_reset_code():
    if current_user.is_authenticated:
        return redirect(url_for("main.dashboard"))

    form = VerifyResetCodeForm()
    retailer_id = session.get("password_reset_retailer_id")
    retailer = db.session.get(Retailer, retailer_id) if retailer_id else None
    reset_code = (
        PasswordResetCode.query.filter_by(retailer_id=retailer.retailer_id, used_at=None)
        .order_by(PasswordResetCode.created_at.desc())
        .first()
        if retailer
        else None
    )

    if form.validate_on_submit():
        now = datetime.utcnow()
        if (
            not reset_code
            or reset_code.expires_at <= now
            or reset_code.attempts >= current_app.config["PASSWORD_RESET_MAX_ATTEMPTS"]
        ):
            flash("That verification code is invalid or expired. Request a new code.", "danger")
        elif not check_password_hash(reset_code.code_hash, form.code.data):
            reset_code.attempts += 1
            db.session.commit()
            flash("That verification code is invalid or expired. Request a new code.", "danger")
        else:
            reset_code.verified_at = now
            db.session.commit()
            session["password_reset_id"] = reset_code.reset_id
            return redirect(url_for("auth.reset_password"))

    return render_template(
        "auth/verify_reset_code.html",
        form=form,
        resend_form=ResendVerificationForm(),
        code_minutes=current_app.config["PASSWORD_RESET_CODE_MINUTES"],
    )


@auth_bp.route("/reset-password", methods=["GET", "POST"])
def reset_password():
    if current_user.is_authenticated:
        return redirect(url_for("main.dashboard"))

    form = ResetPasswordForm()
    reset_id = session.get("password_reset_id")
    reset_code = db.session.get(PasswordResetCode, reset_id) if reset_id else None
    valid_reset = reset_code and not reset_code.used_at and reset_code.verified_at
    if not valid_reset or reset_code.expires_at <= datetime.utcnow():
        session.pop("password_reset_id", None)
        flash("Your reset session has expired. Request a new code.", "warning")
        return redirect(url_for("auth.forgot_password"))

    if form.validate_on_submit():
        retailer = db.session.get(Retailer, reset_code.retailer_id)
        retailer.set_password(form.password.data)
        reset_code.used_at = datetime.utcnow()
        db.session.commit()
        session.pop("password_reset_id", None)
        session.pop("password_reset_retailer_id", None)
        flash("Your password has been updated. You can now log in.", "success")
        return redirect(url_for("auth.login"))

    return render_template("auth/reset_password.html", form=form)


@auth_bp.route("/logout", methods=["POST"])
@login_required
def logout():
    logout_user()
    session.clear()
    flash("You have been logged out.", "info")
    return redirect(url_for("main.index"))
