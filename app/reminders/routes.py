from flask import Blueprint, current_app, render_template, redirect, url_for, flash
from flask_login import login_required, current_user

from app.extensions import db
from app.models import ReminderRule, MessageDispatchLog, Customer
from app.reminders.forms import ReminderActionForm, ReminderRuleForm
from app.reminders.engine import run_ageing_analysis_and_dispatch
from app.permissions import admin_required

reminders_bp = Blueprint("reminders", __name__, url_prefix="/reminders")


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
    logs = (
        MessageDispatchLog.query.join(Customer)
        .filter(Customer.retailer_id == current_user.retailer_id)
        .order_by(MessageDispatchLog.sent_at.desc())
        .limit(100)
        .all()
    )
    return render_template("reminders/log.html", logs=logs)
