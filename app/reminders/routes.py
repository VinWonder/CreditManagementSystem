from flask import Blueprint, render_template, redirect, url_for, flash
from flask_login import login_required, current_user

from app.extensions import db
from app.models import ReminderRule, MessageDispatchLog, Customer
from app.reminders.forms import ReminderRuleForm
from app.reminders.engine import run_ageing_analysis_and_dispatch

reminders_bp = Blueprint("reminders", __name__, url_prefix="/reminders")


@reminders_bp.route("/rules")
@login_required
def list_rules():
    rules = ReminderRule.query.filter_by(retailer_id=current_user.retailer_id).order_by(
        ReminderRule.trigger_days_overdue
    ).all()
    return render_template("reminders/rules.html", rules=rules)


@reminders_bp.route("/rules/new", methods=["GET", "POST"])
@login_required
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
def delete_rule(rule_id):
    rule = ReminderRule.query.get_or_404(rule_id)
    if rule.retailer_id == current_user.retailer_id:
        db.session.delete(rule)
        db.session.commit()
        flash("Reminder rule deleted.", "info")
    return redirect(url_for("reminders.list_rules"))


@reminders_bp.route("/run-now", methods=["POST"])
@login_required
def run_now():
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
