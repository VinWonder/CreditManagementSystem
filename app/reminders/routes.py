import re
from datetime import datetime

from flask import (
    Blueprint,
    current_app,
    flash,
    jsonify,
    redirect,
    render_template,
    request,
    url_for,
)
from flask_login import login_required, current_user
from sqlalchemy import or_
from sqlalchemy.orm import joinedload

from app.extensions import db
from app.models import ReminderRule, MessageDispatchLog, Customer
from app.models import MessagingSettings
from app.reminders.forms import (
    MessagingSettingsForm,
    ReminderActionForm,
    ReminderRuleForm,
)
from app.reminders.engine import (
    render_personal_reminder_message,
    select_matching_rule,
)
from app.reminders.dispatch_service import (
    dispatch_personal_reminder,
    provider_configuration,
)
from app.reminders.credentials import MessagingCredentialError, encrypt_secret
from app.permissions import admin_required

reminders_bp = Blueprint("reminders", __name__, url_prefix="/reminders")


def _has_duplicate_trigger_days(trigger_days, excluding_rule_id=None):
    query = ReminderRule.query.filter_by(
        retailer_id=current_user.retailer_id,
        trigger_days_overdue=trigger_days,
    )
    if excluding_rule_id is not None:
        query = query.filter(ReminderRule.rule_id != excluding_rule_id)
    return query.first() is not None


def _encrypted_secret_update(existing, submitted, clear):
    if clear:
        return None
    return encrypt_secret(submitted) if submitted else existing


def _form_value(field):
    return (field.data or "").strip() or None


@reminders_bp.route("/settings", methods=["GET", "POST"])
@login_required
@admin_required
def messaging_settings():
    settings = MessagingSettings.query.filter_by(
        retailer_id=current_user.retailer_id
    ).first()
    form = MessagingSettingsForm()

    if request.method == "GET":
        if settings:
            form.reminder_sender_number.data = settings.reminder_sender_number or ""
            form.sms_provider.data = settings.sms_provider
            form.at_username.data = settings.at_username or ""
            form.twilio_account_sid.data = settings.twilio_account_sid or ""
            form.twilio_from_number.data = settings.twilio_from_number or ""
            form.twilio_messaging_service_sid.data = (
                settings.twilio_messaging_service_sid or ""
            )
            form.whatsapp_phone_id.data = settings.whatsapp_phone_id or ""

    if form.validate_on_submit():
        if settings is None:
            settings = MessagingSettings(retailer_id=current_user.retailer_id)

        try:
            at_api_key = _encrypted_secret_update(
                settings.at_api_key_encrypted,
                form.at_api_key.data,
                form.clear_at_api_key.data,
            )
            twilio_auth_token = _encrypted_secret_update(
                settings.twilio_auth_token_encrypted,
                form.twilio_auth_token.data,
                form.clear_twilio_auth_token.data,
            )
            whatsapp_token = _encrypted_secret_update(
                settings.whatsapp_token_encrypted,
                form.whatsapp_token.data,
                form.clear_whatsapp_token.data,
            )
        except MessagingCredentialError as exc:
            flash(str(exc), "danger")
            return render_template(
                "reminders/settings.html",
                form=form,
                providers=provider_configuration(current_user.retailer_id),
                settings=settings,
                encryption_key_configured=bool(
                    current_app.config.get("MESSAGING_CREDENTIALS_ENCRYPTION_KEY")
                ),
            )

        settings.sms_provider = form.sms_provider.data
        settings.reminder_sender_number = _form_value(form.reminder_sender_number)
        settings.at_username = _form_value(form.at_username)
        settings.at_api_key_encrypted = at_api_key
        settings.twilio_account_sid = _form_value(form.twilio_account_sid)
        settings.twilio_auth_token_encrypted = twilio_auth_token
        settings.twilio_from_number = _form_value(form.twilio_from_number)
        settings.twilio_messaging_service_sid = _form_value(
            form.twilio_messaging_service_sid
        )
        settings.whatsapp_phone_id = _form_value(form.whatsapp_phone_id)
        settings.whatsapp_token_encrypted = whatsapp_token
        settings.updated_at = datetime.utcnow()
        db.session.add(settings)
        db.session.commit()
        flash("Your provider settings were saved securely.", "success")
        return redirect(url_for("reminders.messaging_settings"))

    return render_template(
        "reminders/settings.html",
        form=form,
        providers=provider_configuration(current_user.retailer_id),
        settings=settings,
        encryption_key_configured=bool(
            current_app.config.get("MESSAGING_CREDENTIALS_ENCRYPTION_KEY")
        ),
    )


@reminders_bp.route("/rules")
@login_required
def list_rules():
    rules = ReminderRule.query.filter_by(retailer_id=current_user.retailer_id).order_by(
        ReminderRule.trigger_days_overdue
    ).all()
    return render_template(
        "reminders/rules.html",
        rules=rules,
        active_count=sum(rule.active for rule in rules),
        inactive_count=sum(not rule.active for rule in rules),
        action_form=ReminderActionForm(),
    )


@reminders_bp.route("/rules/new", methods=["GET", "POST"])
@login_required
@admin_required
def new_rule():
    form = ReminderRuleForm()
    if form.validate_on_submit():
        if _has_duplicate_trigger_days(form.trigger_days_overdue.data):
            form.trigger_days_overdue.errors.append(
                "A rule already exists for this overdue threshold. Edit that rule instead."
            )
            return render_template("reminders/form.html", form=form, title="New Reminder Rule")

        rule = ReminderRule(
            retailer_id=current_user.retailer_id,
            trigger_days_overdue=form.trigger_days_overdue.data,
            channel=form.channel.data,
            message_template=form.message_template.data,
            active=form.active.data,
        )
        db.session.add(rule)
        db.session.commit()
        flash("Reminder rule created.", "success")
        return redirect(url_for("reminders.list_rules"))
    return render_template("reminders/form.html", form=form, title="New Reminder Rule")


@reminders_bp.route("/rules/<int:rule_id>/edit", methods=["GET", "POST"])
@login_required
@admin_required
def edit_rule(rule_id):
    rule = ReminderRule.query.get_or_404(rule_id)
    if rule.retailer_id != current_user.retailer_id:
        flash("Access denied.", "danger")
        return redirect(url_for("reminders.list_rules"))

    form = ReminderRuleForm(obj=rule)
    if form.validate_on_submit():
        if _has_duplicate_trigger_days(
            form.trigger_days_overdue.data,
            excluding_rule_id=rule.rule_id,
        ):
            form.trigger_days_overdue.errors.append(
                "A rule already exists for this overdue threshold. Choose another threshold."
            )
            return render_template("reminders/form.html", form=form, title="Edit Reminder Rule")

        rule.trigger_days_overdue = form.trigger_days_overdue.data
        rule.channel = form.channel.data
        rule.message_template = form.message_template.data
        rule.active = form.active.data
        db.session.commit()
        flash("Reminder rule updated.", "success")
        return redirect(url_for("reminders.list_rules"))
    return render_template("reminders/form.html", form=form, title="Edit Reminder Rule")


@reminders_bp.route("/rules/<int:rule_id>/delete", methods=["POST"])
@login_required
@admin_required
def delete_rule(rule_id):
    rule = ReminderRule.query.get_or_404(rule_id)
    if rule.retailer_id != current_user.retailer_id:
        flash("Access denied.", "danger")
        return redirect(url_for("reminders.list_rules"))

    form = ReminderActionForm()
    if not form.validate_on_submit():
        flash("The delete request could not be verified. Please try again.", "danger")
        return redirect(url_for("reminders.list_rules"))

    db.session.delete(rule)
    db.session.commit()
    flash("Reminder rule deleted.", "info")
    return redirect(url_for("reminders.list_rules"))


@reminders_bp.route("/customers/<int:customer_id>/send-personal", methods=["POST"])
@login_required
@admin_required
def send_personal_reminder(customer_id):
    form = ReminderActionForm()
    if not form.validate_on_submit():
        flash("The reminder request could not be verified. Please try again.", "danger")
        return redirect(url_for("customers.view_customer", customer_id=customer_id))

    customer = Customer.query.filter_by(
        customer_id=customer_id,
        retailer_id=current_user.retailer_id,
    ).first_or_404()
    if customer.preferred_channel not in {"sms", "whatsapp", "both"}:
        flash(
            "This customer's reminder preference is missing or invalid. "
            "Update their contact preferences before sending.",
            "warning",
        )
        return redirect(url_for("customers.view_customer", customer_id=customer_id))

    ledger = customer.ledger
    if not ledger:
        flash("This customer does not have an account balance to remind them about.", "warning")
        return redirect(url_for("customers.view_customer", customer_id=customer_id))

    ledger.recalculate()
    if ledger.outstanding_balance <= 0:
        db.session.rollback()
        flash("This customer has no outstanding balance to remind them about.", "info")
        return redirect(url_for("customers.view_customer", customer_id=customer_id))

    rules = ReminderRule.query.filter_by(
        retailer_id=current_user.retailer_id,
        active=True,
    ).all()
    rule = select_matching_rule(rules, ledger.days_overdue)
    if not rule:
        flash(
            "No active reminder rule matches this customer's overdue period. "
            "No message was sent.",
            "warning",
        )
        return redirect(url_for("customers.view_customer", customer_id=customer_id))

    message = render_personal_reminder_message(rule, customer, current_user, ledger)
    accepted, _ = dispatch_personal_reminder(customer, message, rule_id=rule.rule_id)
    if accepted:
        flash(
            f"Personal reminder accepted by the provider for {customer.full_name} via their saved "
            f"{customer.preferred_channel.replace('_', ' ')} preference.",
            "success",
        )
    else:
        flash(
            f"Reminder attempted for {customer.full_name}, but one or more channels "
            "were not accepted by the provider. Check the dispatch history.",
            "warning",
        )
    return redirect(url_for("reminders.dispatch_log"))


@reminders_bp.route("/api/providers/status")
@login_required
def provider_status():
    """Return configured provider names and readiness, never credentials."""
    return jsonify(providers=provider_configuration(current_user.retailer_id))


@reminders_bp.route("/api/customers/<int:customer_id>/test", methods=["POST"])
@login_required
def test_customer_reminder_provider(customer_id):
    """Send a confirmed, real provider test using the applicable rule template."""
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict) or payload.get("confirm") is not True:
        return jsonify(error="Confirm that this will send a real message to the customer."), 400

    channel = payload.get("channel")
    if not isinstance(channel, str) or channel not in {"sms", "whatsapp"}:
        return jsonify(error="Choose either 'sms' or 'whatsapp' as the test channel."), 400

    customer = Customer.query.filter_by(
        customer_id=customer_id,
        retailer_id=current_user.retailer_id,
    ).first_or_404()
    if not re.fullmatch(r"\+?\d{9,15}", customer.phone_number or ""):
        return jsonify(error="The customer's phone number is invalid. Update it before testing."), 400
    if customer.preferred_channel not in {channel, "both"}:
        return jsonify(
            error=f"The customer has not selected {channel.upper()} as an approved reminder channel."
        ), 400

    ledger = customer.ledger
    if not ledger:
        return jsonify(error="This customer does not have an account balance to remind them about."), 409

    ledger.recalculate()
    if ledger.outstanding_balance <= 0:
        db.session.rollback()
        return jsonify(error="This customer has no outstanding balance to remind them about."), 409

    rules = ReminderRule.query.filter_by(
        retailer_id=current_user.retailer_id,
        active=True,
    ).all()
    rule = select_matching_rule(rules, ledger.days_overdue)
    if not rule:
        return jsonify(
            error="No active reminder rule matches this customer's overdue period."
        ), 409

    channel_provider = provider_configuration(current_user.retailer_id)[channel]
    configured = channel_provider["configured"]
    if not configured:
        return jsonify(
            error=(
                f"{channel_provider['provider']} is not configured. "
                "Add its credentials before sending a live test."
            )
        ), 503

    message = render_personal_reminder_message(rule, customer, current_user, ledger)
    accepted, detail = dispatch_personal_reminder(
        customer, message, rule_id=rule.rule_id, channel=channel
    )
    if not accepted:
        return jsonify(error="The provider did not accept the message.", detail=detail), 502

    return jsonify(
        status="accepted",
        channel=channel,
        rule_id=rule.rule_id,
        message="The provider accepted the message. This does not confirm handset delivery.",
    ), 202


@reminders_bp.route("/log")
@login_required
def dispatch_log():
    base_query = MessageDispatchLog.query.join(Customer).filter(
        Customer.retailer_id == current_user.retailer_id
    )
    total_count = base_query.count()
    sent_count = base_query.filter(MessageDispatchLog.status == "sent").count()
    failed_count = base_query.filter(MessageDispatchLog.status == "failed").count()

    status = request.args.get("status", "").strip().lower()
    if status not in {"", "sent", "failed", "pending"}:
        status = ""

    channel = request.args.get("channel", "").strip().lower()
    if channel not in {"", "sms", "whatsapp"}:
        channel = ""

    search = request.args.get("q", "").strip()[:100]
    filtered_query = base_query
    if status:
        filtered_query = filtered_query.filter(MessageDispatchLog.status == status)
    if channel:
        filtered_query = filtered_query.filter(MessageDispatchLog.channel == channel)
    if search:
        search_term = f"%{search}%"
        filtered_query = filtered_query.filter(
            or_(
                Customer.full_name.ilike(search_term),
                Customer.phone_number.ilike(search_term),
                MessageDispatchLog.message_content.ilike(search_term),
            )
        )

    page = filtered_query.options(
        joinedload(MessageDispatchLog.customer),
        joinedload(MessageDispatchLog.rule),
    ).order_by(
        MessageDispatchLog.sent_at.desc(),
        MessageDispatchLog.dispatch_id.desc(),
    ).paginate(
        page=request.args.get("page", 1, type=int) or 1,
        per_page=25,
        error_out=False,
    )
    return render_template(
        "reminders/log.html",
        page=page,
        logs=page.items,
        total_count=total_count,
        sent_count=sent_count,
        failed_count=failed_count,
        filters={"status": status, "channel": channel, "q": search},
    )
