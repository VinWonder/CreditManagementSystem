from flask import Blueprint, render_template
from flask_login import login_required, current_user
from sqlalchemy import func

from app.extensions import db
from app.models import Customer, CreditTransaction, Payment, Ledger

main_bp = Blueprint("main", __name__)


@main_bp.route("/")
def index():
    return render_template("main/index.html")


@main_bp.route("/features")
def features():
    return render_template("main/features.html")


@main_bp.route("/dashboard")
@login_required
def dashboard():
    retailer_id = current_user.retailer_id

    total_customers = Customer.query.filter_by(retailer_id=retailer_id).count()

    total_outstanding = (
        db.session.query(func.coalesce(func.sum(Ledger.outstanding_balance), 0))
        .filter(Ledger.retailer_id == retailer_id)
        .scalar()
    )

    overdue_customers = (
        Ledger.query.filter(
            Ledger.retailer_id == retailer_id,
            Ledger.outstanding_balance > 0,
            Ledger.days_overdue > 0,
        ).count()
    )

    recent_transactions = (
        CreditTransaction.query.filter_by(retailer_id=retailer_id)
        .order_by(CreditTransaction.transaction_date.desc())
        .limit(8)
        .all()
    )

    recent_payments = (
        Payment.query.filter_by(retailer_id=retailer_id)
        .order_by(Payment.payment_date.desc())
        .limit(8)
        .all()
    )

    # Ageing buckets for the chart
    buckets = {"0-7": 0, "8-14": 0, "15-30": 0, "31-60": 0, "61+": 0}
    ledgers = Ledger.query.filter(
        Ledger.retailer_id == retailer_id, Ledger.outstanding_balance > 0
    ).all()
    for l in ledgers:
        d = l.days_overdue
        amt = float(l.outstanding_balance)
        if d <= 7:
            buckets["0-7"] += amt
        elif d <= 14:
            buckets["8-14"] += amt
        elif d <= 30:
            buckets["15-30"] += amt
        elif d <= 60:
            buckets["31-60"] += amt
        else:
            buckets["61+"] += amt

    return render_template(
        "main/dashboard.html",
        total_customers=total_customers,
        total_outstanding=total_outstanding,
        overdue_customers=overdue_customers,
        recent_transactions=recent_transactions,
        recent_payments=recent_payments,
        buckets=buckets,
    )
