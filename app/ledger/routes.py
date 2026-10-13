from decimal import Decimal

from flask import Blueprint, render_template, request
from flask_login import login_required, current_user

from app.models import Ledger

ledger_bp = Blueprint("ledger", __name__, url_prefix="/ledger")


@ledger_bp.route("/report")
@login_required
def receivables_report():
    requested_sort = request.args.get("sort", "days_overdue")
    sort = requested_sort if requested_sort in {"days_overdue", "balance"} else "days_overdue"
    overdue_only = request.args.get("overdue") == "1"
    base_query = Ledger.query.filter_by(retailer_id=current_user.retailer_id).filter(
        Ledger.outstanding_balance > 0
    )
    all_ledgers = base_query.all()
    total_outstanding = sum(
        (Decimal(ledger.outstanding_balance) for ledger in all_ledgers), Decimal("0")
    )
    overdue_ledgers = [ledger for ledger in all_ledgers if ledger.days_overdue > 0]
    total_overdue = sum(
        (Decimal(ledger.outstanding_balance) for ledger in overdue_ledgers), Decimal("0")
    )

    ledgers = [
        ledger for ledger in all_ledgers if not overdue_only or ledger.days_overdue > 0
    ]
    if sort == "balance":
        ledgers.sort(
            key=lambda ledger: (ledger.outstanding_balance, ledger.days_overdue),
            reverse=True,
        )
    else:
        ledgers.sort(
            key=lambda ledger: (ledger.days_overdue, ledger.outstanding_balance),
            reverse=True,
        )

    return render_template(
        "ledger/report.html",
        ledgers=ledgers,
        total_outstanding=total_overdue if overdue_only else total_outstanding,
        total_overdue=total_overdue,
        overdue_count=len(overdue_ledgers),
        account_count=len(all_ledgers),
        sort=sort,
        overdue_only=overdue_only,
    )
