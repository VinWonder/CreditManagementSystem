from flask import Blueprint, current_app, render_template, redirect, url_for, flash, request
from flask_login import login_required, current_user
from sqlalchemy import or_
from sqlalchemy.orm import joinedload

from app.extensions import db
from app.models import ReminderRule, MessageDispatchLog, Customer
from app.reminders.forms import ReminderActionForm, ReminderRuleForm
from app.reminders.engine import run_ageing_analysis_and_dispatch
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
        daily_schedule=(
            f"{current_app.config.get('REMINDER_SCAN_HOUR', 8):02d}:"
            f"{current_app.config.get('REMINDER_SCAN_MINUTE', 0):02d}"
        ),
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


@reminders_bp.route("/run-now", methods=["POST"])
@login_required
def run_now():
    form = ReminderActionForm()
    if not form.validate_on_submit():
        flash("The scan request could not be verified. Please try again.", "danger")
        return redirect(url_for("reminders.list_rules"))

    active_rules = ReminderRule.query.filter_by(
        retailer_id=current_user.retailer_id, active=True
    ).count()
    if not active_rules:
        flash("There are no active reminder rules to run. Activate or create a rule first.", "warning")
        return redirect(url_for("reminders.list_rules"))

    summary = run_ageing_analysis_and_dispatch(retailer_id=current_user.retailer_id)
    flash(
        f"Ageing analysis complete: {summary['scanned']} accounts scanned, "
        f"{summary['sent']} reminders sent, {summary['failed']} failed.",
        "info",
    )
    return redirect(url_for("reminders.dispatch_log"))


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
