from decimal import Decimal

from datetime import datetime
from flask import Blueprint, render_template, redirect, url_for, flash
from flask_login import login_required, current_user
from sqlalchemy import func

from app.extensions import db
from app.models import (
    BalanceAdjustment,
    CreditTransaction,
    Customer,
    Ledger,
    Payment,
)
from app.payments.forms import BalanceAdjustmentForm, PaymentDecisionForm, PaymentForm
from app.permissions import admin_required

payments_bp = Blueprint("payments", __name__, url_prefix="/payments")


def _outstanding_balances(retailer_id, include_pending=False):
    credit_rows = (
        db.session.query(
            CreditTransaction.customer_id,
            func.sum(CreditTransaction.amount).label("total_credit"),
        )
        .filter(CreditTransaction.retailer_id == retailer_id)
        .group_by(CreditTransaction.customer_id)
        .all()
    )
    payment_query = (
        db.session.query(
            Payment.customer_id,
            func.sum(Payment.amount_paid).label("total_paid"),
        )
        .filter(Payment.retailer_id == retailer_id)
    )
    if include_pending:
        payment_query = payment_query.filter(
            Payment.approval_status.in_(("approved", "pending"))
        )
    else:
        payment_query = payment_query.filter(Payment.approval_status == "approved")
    payment_rows = payment_query.group_by(Payment.customer_id).all()

    adjustment_rows = (
        db.session.query(
            BalanceAdjustment.customer_id,
            func.sum(BalanceAdjustment.amount_change).label("total_change"),
        )
        .filter(BalanceAdjustment.retailer_id == retailer_id)
        .group_by(BalanceAdjustment.customer_id)
        .all()
    )

    balances = {
        customer_id: Decimal(total_credit)
        for customer_id, total_credit in credit_rows
    }
    for customer_id, total_paid in payment_rows:
        balances[customer_id] = balances.get(customer_id, Decimal("0")) - Decimal(total_paid)
    for customer_id, total_change in adjustment_rows:
        balances[customer_id] = balances.get(customer_id, Decimal("0")) + Decimal(total_change)
    return {customer_id: balance for customer_id, balance in balances.items() if balance > 0}


@payments_bp.route("/")
@login_required
def list_payments():
    payments = (
        Payment.query.filter_by(retailer_id=current_user.retailer_id)
        .order_by(Payment.payment_date.desc())
        .all()
    )
    return render_template(
        "payments/list.html",
        payments=payments,
        total_paid=sum(
            (
                payment.amount_paid
                for payment in payments
                if payment.approval_status == "approved"
            ),
            Decimal("0"),
        ),
        customer_count=len(
            {
                payment.customer_id
                for payment in payments
                if payment.approval_status == "approved"
            }
        ),
        pending_count=sum(payment.approval_status == "pending" for payment in payments),
        pending_total=sum(
            (
                payment.amount_paid
                for payment in payments
                if payment.approval_status == "pending"
            ),
            Decimal("0"),
        ),
        decision_forms={
            payment.payment_id: PaymentDecisionForm()
            for payment in payments
            if payment.approval_status == "pending"
        },
    )


@payments_bp.route("/new", methods=["GET", "POST"])
@login_required
def new_payment():
    form = PaymentForm()
    customers = Customer.query.filter_by(retailer_id=current_user.retailer_id).order_by(
        Customer.full_name
    ).all()
    balances = _outstanding_balances(current_user.retailer_id, include_pending=True)
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
        current_balances = _outstanding_balances(
            current_user.retailer_id, include_pending=True
        )
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
                approval_status="pending" if not current_user.is_admin else "approved",
                submitted_by_shopkeeper_id=(
                    current_user.shopkeeper_id if not current_user.is_admin else None
                ),
                reviewed_by_retailer_id=(
                    current_user.retailer_id if current_user.is_admin else None
                ),
                reviewed_at=datetime.utcnow() if current_user.is_admin else None,
            )
            db.session.add(payment)

            ledger = Ledger.query.filter_by(customer_id=customer.customer_id).first()
            if not ledger:
                ledger = Ledger(retailer_id=current_user.retailer_id, customer_id=customer.customer_id)
                db.session.add(ledger)
            db.session.flush()
            ledger.recalculate()
            remaining_balance = Decimal(ledger.outstanding_balance)
            db.session.commit()

            if current_user.is_admin:
                message = (
                    "Payment logged. Customer balance is now marked cleared."
                    if remaining_balance <= 0
                    else f"Payment logged. Outstanding balance: KES {remaining_balance:,.2f}."
                )
            else:
                message = (
                    f"Payment submitted for admin approval. The ledger remains "
                    f"KES {remaining_balance:,.2f} due until approved."
                )
            flash(message, "success")
            return redirect(url_for("payments.list_payments"))

    return render_template(
        "payments/form.html",
        form=form,
        customers_available=bool(owing_customers),
        has_customers=bool(customers),
        balances=balances,
    )


@payments_bp.route("/<int:payment_id>/approve", methods=["POST"])
@login_required
@admin_required
def approve_payment(payment_id):
    form = PaymentDecisionForm()
    payment = Payment.query.filter_by(
        payment_id=payment_id, retailer_id=current_user.retailer_id
    ).with_for_update().first_or_404()
    if not form.validate_on_submit():
        flash("The approval request could not be verified. Please try again.", "danger")
        return redirect(url_for("payments.list_payments"))
    if payment.approval_status != "pending":
        flash("This payment has already been reviewed.", "warning")
        return redirect(url_for("payments.list_payments"))

    customer = Customer.query.filter_by(
        customer_id=payment.customer_id,
        retailer_id=current_user.retailer_id,
    ).with_for_update().first_or_404()
    outstanding = _outstanding_balances(current_user.retailer_id).get(
        payment.customer_id, Decimal("0")
    )
    if payment.amount_paid > outstanding:
        flash(
            "This payment cannot be approved because the customer's current balance "
            "is lower than the submitted amount. Reject it or correct the balance first.",
            "danger",
        )
        return redirect(url_for("payments.list_payments"))

    payment.approval_status = "approved"
    payment.reviewed_by_retailer_id = current_user.retailer_id
    payment.reviewed_at = datetime.utcnow()
    payment.review_note = form.review_note.data.strip() if form.review_note.data else None
    ledger = Ledger.query.filter_by(customer_id=payment.customer_id).first()
    if not ledger:
        ledger = Ledger(
            retailer_id=current_user.retailer_id,
            customer_id=customer.customer_id,
        )
        db.session.add(ledger)
    db.session.flush()
    ledger.recalculate()
    remaining_balance = Decimal(ledger.outstanding_balance)
    db.session.commit()
    flash(
        "Payment approved. Customer balance is now marked cleared."
        if remaining_balance <= 0
        else f"Payment approved. Outstanding balance: KES {remaining_balance:,.2f}.",
        "success",
    )
    return redirect(url_for("payments.list_payments"))


@payments_bp.route("/<int:payment_id>/reject", methods=["POST"])
@login_required
@admin_required
def reject_payment(payment_id):
    form = PaymentDecisionForm()
    payment = Payment.query.filter_by(
        payment_id=payment_id, retailer_id=current_user.retailer_id
    ).first_or_404()
    if not form.validate_on_submit():
        flash("The rejection request could not be verified. Please try again.", "danger")
        return redirect(url_for("payments.list_payments"))
    if payment.approval_status != "pending":
        flash("This payment has already been reviewed.", "warning")
    elif not (form.review_note.data or "").strip():
        flash("Enter a reason before rejecting this payment.", "danger")
    else:
        payment.approval_status = "rejected"
        payment.reviewed_by_retailer_id = current_user.retailer_id
        payment.reviewed_at = datetime.utcnow()
        payment.review_note = form.review_note.data.strip()
        db.session.commit()
        flash("Payment rejected; the customer's balance was not changed.", "info")
    return redirect(url_for("payments.list_payments"))


@payments_bp.route(
    "/customers/<int:customer_id>/adjust-balance", methods=["POST"]
)
@login_required
@admin_required
def adjust_customer_balance(customer_id):
    customer = (
        Customer.query.filter_by(
            customer_id=customer_id, retailer_id=current_user.retailer_id
        )
        .with_for_update()
        .first_or_404()
    )
    form = BalanceAdjustmentForm()
    if not form.validate_on_submit():
        flash("Enter a valid adjustment amount and reason.", "danger")
        return redirect(url_for("customers.view_customer", customer_id=customer_id))

    ledger = Ledger.query.filter_by(customer_id=customer.customer_id).first()
    if not ledger:
        ledger = Ledger(retailer_id=current_user.retailer_id, customer_id=customer.customer_id)
        db.session.add(ledger)
        db.session.flush()
    ledger.recalculate()
    resulting_balance = ledger.outstanding_balance + form.amount_change.data
    if resulting_balance < 0:
        form.amount_change.errors.append("The correction cannot create a negative customer balance.")
        flash(form.amount_change.errors[0], "danger")
        return redirect(url_for("customers.view_customer", customer_id=customer_id))

    db.session.add(
        BalanceAdjustment(
            retailer_id=current_user.retailer_id,
            customer_id=customer.customer_id,
            amount_change=form.amount_change.data,
            reason=form.reason.data.strip(),
            created_by_retailer_id=current_user.retailer_id,
        )
    )
    db.session.flush()
    ledger.recalculate()
    remaining_balance = Decimal(ledger.outstanding_balance)
    db.session.commit()
    flash(
        "Balance correction saved. Customer account is now marked cleared."
        if remaining_balance <= 0
        else f"Balance correction saved. Outstanding balance: KES {remaining_balance:,.2f}.",
        "success",
    )
    return redirect(url_for("customers.view_customer", customer_id=customer_id))
