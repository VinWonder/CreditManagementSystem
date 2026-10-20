from flask import Blueprint, render_template, redirect, url_for, flash, request
from flask_login import login_required, current_user

from app.extensions import db
from app.models import CreditTransaction, Customer, Ledger, ReminderRule
from app.customers.forms import CustomerForm
from app.payments.forms import BalanceAdjustmentForm
from app.reminders.forms import ReminderActionForm
from app.reminders.engine import select_matching_rule, render_personal_reminder_message
from app.reminders.links import reminder_links
from app.permissions import admin_required

customers_bp = Blueprint("customers", __name__, url_prefix="/customers")


def _owned_customer_or_404(customer_id):
    customer = Customer.query.get_or_404(customer_id)
    if customer.retailer_id != current_user.retailer_id:
        flash("You do not have access to that customer record.", "danger")
        return None
    return customer


def _customer_ids_with_credit_sales(customers):
    customer_ids = [customer.customer_id for customer in customers]
    if not customer_ids:
        return set()

    rows = (
        db.session.query(CreditTransaction.customer_id)
        .filter(CreditTransaction.customer_id.in_(customer_ids))
        .distinct()
        .all()
    )
    return {customer_id for customer_id, in rows}


@customers_bp.route("/")
@login_required
def list_customers():
    query = request.args.get("q", "").strip()
    base = Customer.query.filter_by(retailer_id=current_user.retailer_id)
    if query:
        base = base.filter(
            (Customer.full_name.ilike(f"%{query}%"))
            | (Customer.phone_number.ilike(f"%{query}%"))
            | (Customer.shop_name.ilike(f"%{query}%"))
        )
    customers = base.order_by(Customer.full_name.asc()).all()
    customers_with_purchases = _customer_ids_with_credit_sales(customers)
    return render_template(
        "customers/list.html",
        customers=customers,
        customers_with_purchases=customers_with_purchases,
        query=query,
    )


@customers_bp.route("/new", methods=["GET", "POST"])
@login_required
def new_customer():
    form = CustomerForm()
    if form.validate_on_submit():
        customer = Customer(
            retailer_id=current_user.retailer_id,
            full_name=form.full_name.data,
            phone_number=form.phone_number.data,
            shop_name=(form.shop_name.data or "").strip() or None,
            preferred_channel=form.preferred_channel.data,
        )
        db.session.add(customer)
        db.session.flush()  # get customer_id before commit

        ledger = Ledger(
            retailer_id=current_user.retailer_id,
            customer_id=customer.customer_id,
            outstanding_balance=0,
            days_overdue=0,
        )
        db.session.add(ledger)
        db.session.commit()

        flash(f"Customer '{customer.full_name}' added.", "success")
        return redirect(url_for("customers.list_customers"))

    return render_template("customers/form.html", form=form, title="Add Customer")


@customers_bp.route("/<int:customer_id>/edit", methods=["GET", "POST"])
@login_required
@admin_required
def edit_customer(customer_id):
    customer = _owned_customer_or_404(customer_id)
    if not customer:
        return redirect(url_for("customers.list_customers"))

    form = CustomerForm(obj=customer)
    if form.validate_on_submit():
        customer.full_name = form.full_name.data
        customer.phone_number = form.phone_number.data
        customer.shop_name = (form.shop_name.data or "").strip() or None
        customer.preferred_channel = form.preferred_channel.data
        db.session.commit()
        flash("Customer updated.", "success")
        return redirect(url_for("customers.list_customers"))

    return render_template("customers/form.html", form=form, title="Edit Customer")


@customers_bp.route("/<int:customer_id>/delete", methods=["POST"])
@login_required
@admin_required
def delete_customer(customer_id):
    customer = _owned_customer_or_404(customer_id)
    if customer:
        db.session.delete(customer)
        db.session.commit()
        flash("Customer removed.", "info")
    return redirect(url_for("customers.list_customers"))


@customers_bp.route("/<int:customer_id>")
@login_required
def view_customer(customer_id):
    customer = _owned_customer_or_404(customer_id)
    if not customer:
        return redirect(url_for("customers.list_customers"))

    transactions = customer.transactions.order_by(
        db.desc("transaction_date")
    ).all()
    payments = customer.payments.order_by(db.desc("payment_date")).all()
    adjustments = customer.balance_adjustments.order_by(db.desc("created_at")).all()
    matching_rule = None
    active_rules = []
    links = {"whatsapp": None, "sms": None}
    if customer.ledger:
        customer.ledger.recalculate()
        active_rules = ReminderRule.query.filter_by(
            retailer_id=current_user.retailer_id,
            active=True,
        ).order_by(ReminderRule.trigger_days_overdue).all()
        matching_rule = select_matching_rule(
            active_rules, customer.ledger.days_overdue
        )
        # The retailer opens their own WhatsApp or SMS app and sends the message
        # from their registered number; nothing is sent by the server.
        if matching_rule:
            message = render_personal_reminder_message(
                matching_rule, customer, current_user, customer.ledger
            )
            links = reminder_links(customer, message)
    return render_template(
        "customers/view.html",
        customer=customer,
        transactions=transactions,
        payments=payments,
        adjustments=adjustments,
        matching_rule=matching_rule,
        active_rules=active_rules,
        reminder_links=links,
        adjustment_form=BalanceAdjustmentForm(),
        reminder_form=ReminderActionForm(),
    )
