from datetime import datetime

from flask import Blueprint, current_app, flash, redirect, render_template, url_for
from flask_login import current_user, login_required

from app.extensions import db
from app.models import MpesaSettings
from app.mpesa.daraja import account_reference_for
from app.mpesa.forms import MpesaSettingsForm
from app.permissions import admin_required
from app.reminders.credentials import MessagingCredentialError, encrypt_secret

mpesa_bp = Blueprint("mpesa", __name__, url_prefix="/mpesa")


def callback_base_url():
    """The public HTTPS address Safaricom can reach, or None when not configured."""
    base = current_app.config.get("PUBLIC_BASE_URL") or ""
    return base if base.startswith("https://") else None


@mpesa_bp.route("/settings", methods=["GET", "POST"])
@login_required
@admin_required
def settings():
    saved = MpesaSettings.query.filter_by(retailer_id=current_user.retailer_id).first()
    form = MpesaSettingsForm(obj=saved, has_saved_secrets=saved is not None)
    if form.validate_on_submit():
        try:
            new_secrets = {
                name: encrypt_secret(getattr(form, name).data.strip())
                for name in ("consumer_key", "consumer_secret", "passkey")
                if (getattr(form, name).data or "").strip()
            }
        except MessagingCredentialError as exc:
            flash(f"{exc} Set MESSAGING_CREDENTIALS_ENCRYPTION_KEY in .env.", "danger")
            return render_template("mpesa/settings.html", form=form, saved=saved,
                                   callback_base=callback_base_url(),
                                   account_reference=account_reference_for(current_user.business_name))

        if saved is None:
            saved = MpesaSettings(retailer_id=current_user.retailer_id)
            db.session.add(saved)
        saved.environment = form.environment.data
        saved.account_type = form.account_type.data
        saved.business_shortcode = form.business_shortcode.data.strip()
        saved.till_number = (form.till_number.data or "").strip() or None
        if saved.account_type != "till":
            saved.till_number = None
        for name, value in new_secrets.items():
            setattr(saved, f"{name}_encrypted", value)
        saved.updated_at = datetime.utcnow()
        db.session.commit()
        flash("M-Pesa settings saved. You can now send payment prompts.", "success")
        return redirect(url_for("mpesa.settings"))

    return render_template(
        "mpesa/settings.html",
        form=form,
        saved=saved,
        callback_base=callback_base_url(),
        account_reference=account_reference_for(current_user.business_name),
    )
