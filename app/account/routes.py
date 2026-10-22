"""The signed-in user's own profile: shop details, password and email."""

import os
from datetime import datetime

from flask import Blueprint, current_app, flash, redirect, render_template, url_for
from flask_login import current_user, login_required
from sqlalchemy.exc import IntegrityError
from werkzeug.security import check_password_hash

from app.account.forms import (
    ChangeEmailForm,
    ChangePasswordForm,
    ConfirmEmailForm,
    ProfileForm,
    ResendCodeForm,
)
from app.auth.identity import canonical_email, clean_shop_name, shop_name_key
from app.auth.routes import (
    create_email_verification,
    normalize_phone,
    normalized_email,
    password_guessable_from,
    save_shop_logo,
)
from app.extensions import db
from app.models import EmailVerificationCode, Retailer, Shopkeeper
from app.permissions import admin_required

account_bp = Blueprint("account", __name__, url_prefix="/account")


def _remove_logo_file(filename):
    if not filename:
        return
    try:
        os.remove(os.path.join(current_app.config["SHOP_LOGO_UPLOAD_FOLDER"], filename))
    except OSError:
        pass


@account_bp.route("/profile", methods=["GET", "POST"])
@login_required
def profile():
    if not current_user.is_admin:
        return render_template("account/shopkeeper_profile.html")

    retailer = current_user
    form = ProfileForm(obj=retailer)
    if form.validate_on_submit():
        business_name = clean_shop_name(form.business_name.data)
        owner_name = clean_shop_name(form.owner_name.data)
        try:
            phone_number = normalize_phone(form.phone_number.data)
        except ValueError as exc:
            form.phone_number.errors.append(str(exc))
            return render_template("account/profile.html", form=form)

        if Retailer.query.filter(
            db.func.lower(Retailer.business_name) == shop_name_key(business_name),
            Retailer.retailer_id != retailer.retailer_id,
        ).first():
            form.business_name.errors.append("Another shop already uses this name. Add your area or branch.")
        if phone_number != retailer.phone_number:
            if (
                Retailer.query.filter(
                    Retailer.phone_number == phone_number, Retailer.retailer_id != retailer.retailer_id
                ).first()
                or Shopkeeper.query.filter_by(phone_number=phone_number).first()
            ):
                form.phone_number.errors.append("This phone number is already used by another account.")
            elif not retailer.check_password(form.current_password.data or ""):
                # The phone number is what you sign in with.
                form.current_password.errors.append("Enter your current password to change the phone number.")
        if form.errors:
            return render_template("account/profile.html", form=form)

        old_logo = retailer.logo_filename
        if form.logo.data and getattr(form.logo.data, "filename", ""):
            try:
                retailer.logo_filename = save_shop_logo(form.logo.data)
            except ValueError as exc:
                form.logo.errors.append(str(exc))
                return render_template("account/profile.html", form=form)
        elif form.remove_logo.data:
            retailer.logo_filename = None

        retailer.business_name = business_name
        retailer.owner_name = owner_name
        retailer.phone_number = phone_number
        try:
            db.session.commit()
        except IntegrityError:
            db.session.rollback()
            form.business_name.errors.append("These details were just taken by another account. Please check them.")
            return render_template("account/profile.html", form=form)
        if old_logo and old_logo != retailer.logo_filename:
            _remove_logo_file(old_logo)
        flash("Profile updated.", "success")
        return redirect(url_for("account.profile"))

    return render_template("account/profile.html", form=form)


@account_bp.route("/password", methods=["GET", "POST"])
@login_required
def change_password():
    form = ChangePasswordForm()
    if form.validate_on_submit():
        if not current_user.check_password(form.current_password.data):
            form.current_password.errors.append("That is not your current password.")
        else:
            guessable = password_guessable_from(
                form.password.data, current_user.phone_number, getattr(current_user, "email", None) or ""
            )
            if guessable:
                form.password.errors.append(guessable)
            elif current_user.check_password(form.password.data):
                form.password.errors.append("Choose a password you have not been using.")
            else:
                current_user.set_password(form.password.data)
                db.session.commit()
                flash("Password changed.", "success")
                return redirect(url_for("account.profile"))
    return render_template("account/password.html", form=form)


@account_bp.route("/email", methods=["GET", "POST"])
@login_required
@admin_required
def change_email():
    retailer = current_user
    form = ChangeEmailForm()
    if form.validate_on_submit():
        new_email = normalized_email(form.email.data)
        if not retailer.check_password(form.current_password.data):
            form.current_password.errors.append("That is not your current password.")
        elif retailer.email and canonical_email(new_email) == canonical_email(retailer.email):
            form.email.errors.append("This is already your email.")
        elif Retailer.query.filter(
            Retailer.retailer_id != retailer.retailer_id,
            (Retailer.email == new_email) | (Retailer.email_canonical == canonical_email(new_email)),
        ).first():
            form.email.errors.append("This email is already registered to another shop.")
        else:
            retailer.pending_email = new_email
            db.session.commit()
            category, message = create_email_verification(retailer, to_address=new_email)
            if category == "danger":
                retailer.pending_email = None
                db.session.commit()
                form.email.errors.append(f"We couldn't send a code to {new_email}. Check it and try again.")
            else:
                flash(message, category)
                return redirect(url_for("account.confirm_email"))
    return render_template("account/email.html", form=form)


@account_bp.route("/email/confirm", methods=["GET", "POST"])
@login_required
@admin_required
def confirm_email():
    retailer = current_user
    if not retailer.pending_email:
        return redirect(url_for("account.change_email"))
    form = ConfirmEmailForm()
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
            retailer.email = retailer.pending_email
            retailer.email_canonical = canonical_email(retailer.pending_email)
            retailer.email_verified_at = now
            retailer.pending_email = None
            try:
                db.session.commit()
            except IntegrityError:
                db.session.rollback()
                flash("That email was just registered by another shop.", "danger")
                return redirect(url_for("account.change_email"))
            flash(f"Your email is now {retailer.email}.", "success")
            return redirect(url_for("account.profile"))
    return render_template(
        "account/confirm_email.html",
        form=form,
        resend_form=ResendCodeForm(),
        pending_email=retailer.pending_email,
        code_minutes=current_app.config["EMAIL_VERIFICATION_CODE_MINUTES"],
    )


@account_bp.route("/email/resend", methods=["POST"])
@login_required
@admin_required
def resend_email_code():
    retailer = current_user
    if retailer.pending_email and ResendCodeForm().validate_on_submit():
        category, message = create_email_verification(retailer, to_address=retailer.pending_email)
        flash(message, category)
    return redirect(url_for("account.confirm_email"))
