from flask import Blueprint, render_template, redirect, url_for, flash
from flask_login import login_required, current_user

from app.extensions import db
from app.models import Customer, Payment, Ledger
from app.payments.forms import PaymentForm

payments_bp = Blueprint("payments", __name__, url_prefix="/payments")


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
    form.customer_id.choices = [(c.customer_id, f"{c.full_name} ({c.phone_number})") for c in customers]

    if form.validate_on_submit():
        payment = Payment(
            retailer_id=current_user.retailer_id,
            customer_id=form.customer_id.data,
            amount_paid=form.amount_paid.data,
        )
        db.session.add(payment)

        ledger = Ledger.query.filter_by(customer_id=form.customer_id.data).first()
        if not ledger:
            ledger = Ledger(retailer_id=current_user.retailer_id, customer_id=form.customer_id.data)
            db.session.add(ledger)
        db.session.flush()
        ledger.recalculate()
        db.session.commit()

        flash("Payment logged and ledger balance updated.", "success")
        return redirect(url_for("payments.list_payments"))

    return render_template("payments/form.html", form=form)
