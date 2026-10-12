from flask import Blueprint, render_template, redirect, url_for, flash, request, jsonify
from flask_login import login_required, current_user
from datetime import datetime

from app.extensions import db
from app.models import Customer, CreditTransaction, Ledger
from app.transactions.forms import TransactionForm

transactions_bp = Blueprint("transactions", __name__, url_prefix="/transactions")


def _populate_customer_choices(form):
    customers = Customer.query.filter_by(retailer_id=current_user.retailer_id).order_by(
        Customer.full_name
    ).all()
    form.customer_id.choices = [(c.customer_id, f"{c.full_name} ({c.phone_number})") for c in customers]


def _apply_transaction(retailer_id, customer_id, item_description, amount, sync_status="synced"):
    """Shared logic used by the normal form AND the offline-sync API."""
    txn = CreditTransaction(
        retailer_id=retailer_id,
        customer_id=customer_id,
        item_description=item_description,
        amount=amount,
        sync_status=sync_status,
    )
    db.session.add(txn)

    ledger = Ledger.query.filter_by(customer_id=customer_id).first()
    if not ledger:
        ledger = Ledger(retailer_id=retailer_id, customer_id=customer_id)
        db.session.add(ledger)
    db.session.flush()
    ledger.recalculate()
    db.session.commit()
    return txn


@transactions_bp.route("/")
@login_required
def list_transactions():
    transactions = (
        CreditTransaction.query.filter_by(retailer_id=current_user.retailer_id)
        .order_by(CreditTransaction.transaction_date.desc())
        .all()
    )
    return render_template("transactions/list.html", transactions=transactions)


@transactions_bp.route("/new", methods=["GET", "POST"])
@login_required
def new_transaction():
    form = TransactionForm()
    _populate_customer_choices(form)

    if form.validate_on_submit():
        _apply_transaction(
            current_user.retailer_id,
            form.customer_id.data,
            form.item_description.data,
            form.amount.data,
        )
        flash("Credit sale recorded and ledger updated.", "success")
        return redirect(url_for("transactions.list_transactions"))

    return render_template("transactions/form.html", form=form)


# ---------------------------------------------------------------------------
# Offline-first support: the browser caches transactions recorded while
# offline in IndexedDB (see static/js/offline-db.js) and replays them here
# once connectivity returns (see static/js/sync.js).
# ---------------------------------------------------------------------------
@transactions_bp.route("/api/sync", methods=["POST"])
@login_required
def sync_offline_transactions():
    payload = request.get_json(silent=True) or {}
    pending = payload.get("transactions", [])

    results = []
    for item in pending:
        try:
            customer = Customer.query.filter_by(
                customer_id=item["customer_id"], retailer_id=current_user.retailer_id
            ).first()
            if not customer:
                results.append({"client_id": item.get("client_id"), "status": "failed",
                                 "reason": "unknown customer"})
                continue

            txn = _apply_transaction(
                current_user.retailer_id,
                customer.customer_id,
                item["item_description"],
                item["amount"],
                sync_status="synced",
            )
            results.append(
                {"client_id": item.get("client_id"), "status": "synced", "server_id": txn.transaction_id}
            )
        except Exception as exc:  # noqa: BLE001 - report back to the client
            results.append({"client_id": item.get("client_id"), "status": "failed", "reason": str(exc)})

    return jsonify({"results": results, "synced_at": datetime.utcnow().isoformat()})
