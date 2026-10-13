from flask import Blueprint, render_template, request
from flask_login import login_required, current_user

from app.models import Ledger

ledger_bp = Blueprint("ledger", __name__, url_prefix="/ledger")


@ledger_bp.route("/report")
@login_required
def receivables_report():
    sort = request.args.get("sort", "days_overdue")
    overdue_only = request.args.get("overdue") == "1"
    query = Ledger.query.filter_by(retailer_id=current_user.retailer_id).filter(
        Ledger.outstanding_balance > 0
    )
    if overdue_only:
        query = query.filter(Ledger.days_overdue > 0)

    if sort == "balance":
        query = query.order_by(Ledger.outstanding_balance.desc())
    else:
        query = query.order_by(Ledger.days_overdue.desc())

    ledgers = query.all()

    total_outstanding = sum((float(l.outstanding_balance) for l in ledgers), 0.0)

    return render_template(
        "ledger/report.html",
        ledgers=ledgers,
        total_outstanding=total_outstanding,
        sort=sort,
        overdue_only=overdue_only,
    )
