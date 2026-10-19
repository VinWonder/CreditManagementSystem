from datetime import datetime, timedelta
import os
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
)
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


@auth_bp.route("/register", methods=["GET", "POST"])
def register():
    if current_user.is_authenticated:
        return redirect(url_for("main.dashboard"))

    form = RegisterForm()
    if form.validate_on_submit():
        try:
            phone_number = normalize_phone(form.phone_number.data)
        except ValueError as exc:
            form.phone_number.errors.append(str(exc))
            return render_template("auth/register.html", form=form)
        email = normalized_email(form.email.data)
        existing = Retailer.query.filter_by(phone_number=phone_number).first()
        existing_shopkeeper = Shopkeeper.query.filter_by(phone_number=phone_number).first()
        if existing or existing_shopkeeper:
            flash("That phone number is already registered. Please log in.", "warning")
            return redirect(url_for("auth.login"))
        if Retailer.query.filter_by(email=email).first():
            flash("That email is already registered. Please log in or verify your account.", "warning")
            return redirect(url_for("auth.login"))

        try:
            logo_filename = save_shop_logo(form.logo.data)
        except ValueError as exc:
            form.logo.errors.append(str(exc))
            return render_template("auth/register.html", form=form)

        retailer = Retailer(
            business_name=form.business_name.data,
            owner_name=form.owner_name.data,
            phone_number=phone_number,
            email=email,
            logo_filename=logo_filename,
        )
        retailer.set_password(form.password.data)
        db.session.add(retailer)
        try:
            db.session.commit()
        except IntegrityError:
            db.session.rollback()
            if logo_filename:
                try:
                    os.remove(os.path.join(current_app.config["SHOP_LOGO_UPLOAD_FOLDER"], logo_filename))
                except OSError:
                    pass
            flash("That phone number is already registered. Please log in.", "warning")
            return redirect(url_for("auth.login"))

        flash("Account created. You can now sign in.", "success")
        return redirect(url_for("auth.login"))

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
            session.pop("phone_verification_retailer_id", None)
            session.pop("login_challenge_id", None)
            session.pop("login_next", None)
            next_page = request.args.get("next")
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


@auth_bp.route("/forgot-password", methods=["GET", "POST"])
def forgot_password():
    if current_user.is_authenticated:
        return redirect(url_for("main.dashboard"))

    form = ForgotPasswordForm()
    if form.validate_on_submit():
        try:
            phone_number = normalize_phone(form.phone_number.data)
        except ValueError:
            phone_number = None
        retailer = Retailer.query.filter_by(phone_number=phone_number).first()

        if retailer:
            PasswordResetCode.query.filter_by(
                retailer_id=retailer.retailer_id, used_at=None
            ).delete(synchronize_session=False)
            code = f"{secrets.randbelow(1_000_000):06d}"
            reset_code = PasswordResetCode(
                retailer_id=retailer.retailer_id,
                code_hash=generate_password_hash(code, method="scrypt"),
                expires_at=datetime.utcnow()
                + timedelta(minutes=current_app.config["PASSWORD_RESET_CODE_MINUTES"]),
            )
            db.session.add(reset_code)
            db.session.commit()

            success, info = send_verification_sms(
                retailer.phone_number,
                f"Duka Credit verification code: {code}. It expires in "
                f"{current_app.config['PASSWORD_RESET_CODE_MINUTES']} minutes.",
            )
            if success:
                if info.startswith("simulated"):
                    current_app.logger.info(
                        "Simulated password reset code for retailer %s: %s",
                        retailer.retailer_id,
                        code,
                    )
            else:
                db.session.delete(reset_code)
                db.session.commit()
                current_app.logger.warning("Password reset SMS failed: %s", info)

        session["password_reset_phone"] = phone_number
        flash(
            "If an account matches that phone number, a verification code has been sent.",
            "info",
        )
        return redirect(url_for("auth.verify_reset_code"))

    return render_template("auth/forgot_password.html", form=form)


@auth_bp.route("/verify-reset-code", methods=["GET", "POST"])
def verify_reset_code():
    if current_user.is_authenticated:
        return redirect(url_for("main.dashboard"))

    form = VerifyResetCodeForm()
    phone_number = session.get("password_reset_phone")
    retailer = Retailer.query.filter_by(phone_number=phone_number).first() if phone_number else None
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

    return render_template("auth/verify_reset_code.html", form=form)


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
        session.pop("password_reset_phone", None)
        flash("Your password has been updated. You can now log in.", "success")
        return redirect(url_for("auth.login"))

    return render_template("auth/reset_password.html", form=form)


@auth_bp.route("/logout", methods=["POST"])
@login_required
def logout():
    logout_user()
    session.clear()
    flash("You have been logged out.", "info")
    return redirect(url_for("auth.login"))
