from decimal import Decimal

from flask import Blueprint, render_template, redirect, url_for, flash
from flask_login import login_required, current_user
from sqlalchemy import func

from app.extensions import db
from app.models import CreditTransaction, Customer, Payment, Ledger
from app.payments.forms import PaymentForm

payments_bp = Blueprint("payments", __name__, url_prefix="/payments")


def _outstanding_balances(retailer_id):
    credit_rows = (
        db.session.query(
            CreditTransaction.customer_id,
            func.sum(CreditTransaction.amount).label("total_credit"),
        )
        .filter(CreditTransaction.retailer_id == retailer_id)
        .group_by(CreditTransaction.customer_id)
        .all()
    )
    payment_rows = (
        db.session.query(
            Payment.customer_id,
            func.sum(Payment.amount_paid).label("total_paid"),
        )
        .filter(Payment.retailer_id == retailer_id)
        .group_by(Payment.customer_id)
        .all()
    )

    balances = {
        customer_id: Decimal(total_credit)
        for customer_id, total_credit in credit_rows
    }
    for customer_id, total_paid in payment_rows:
        balances[customer_id] = balances.get(customer_id, Decimal("0")) - Decimal(total_paid)
    return {customer_id: balance for customer_id, balance in balances.items() if balance > 0}


@payments_bp.route("/")
@login_required
def list_payments():
    payments = (
        Payment.query.filter_by(retailer_id=current_user.retailer_id)
        .order_by(Payment.payment_date.desc())
        .all()
    )
    return render_template("payments/list.html", payments=payments)


@payments_bp.route("/new", methods=["GET", "POST"])
@login_required
def new_payment():
    form = PaymentForm()
    customers = Customer.query.filter_by(retailer_id=current_user.retailer_id).order_by(
        Customer.full_name
    ).all()
    balances = _outstanding_balances(current_user.retailer_id)
    owing_customers = [customer for customer in customers if customer.customer_id in balances]
    form.customer_id.choices = [("", "Choose a customer")] + [
        (
            customer.customer_id,
            f"{customer.full_name} ({customer.phone_number}) — KES {balances[customer.customer_id]:,.2f} due",
        )
        for customer in owing_customers
    ]

    if form.validate_on_submit():
        customer = (
            Customer.query.filter_by(
                customer_id=form.customer_id.data,
                retailer_id=current_user.retailer_id,
            )
            .with_for_update()
            .first()
        )
        current_balances = _outstanding_balances(current_user.retailer_id)
        current_balance = current_balances.get(form.customer_id.data, Decimal("0"))
        if customer is None or current_balance <= 0:
            form.customer_id.errors.append("Choose a customer with an outstanding balance.")
        elif form.amount_paid.data > current_balance:
            form.amount_paid.errors.append(
                f"Payment cannot exceed the outstanding balance of "
                f"KES {current_balance:,.2f}."
            )
        else:
            payment = Payment(
                retailer_id=current_user.retailer_id,
                customer_id=customer.customer_id,
                amount_paid=form.amount_paid.data,
            )
            db.session.add(payment)

            ledger = Ledger.query.filter_by(customer_id=customer.customer_id).first()
            if not ledger:
                ledger = Ledger(retailer_id=current_user.retailer_id, customer_id=customer.customer_id)
                db.session.add(ledger)
            db.session.flush()
            ledger.recalculate()
            db.session.commit()

            flash("Payment logged and ledger balance updated.", "success")
            return redirect(url_for("payments.list_payments"))

    return render_template(
        "payments/form.html",
        form=form,
        customers_available=bool(owing_customers),
        has_customers=bool(customers),
        balances=balances,
    )
