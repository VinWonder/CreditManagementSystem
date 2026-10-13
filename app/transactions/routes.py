from flask import Blueprint, render_template, redirect, url_for, flash, request, jsonify
from flask_login import login_required, current_user
from datetime import datetime
from decimal import Decimal, InvalidOperation
from sqlalchemy.exc import SQLAlchemyError

from app.extensions import db
from app.models import Customer, CreditTransaction, Ledger
from app.transactions.forms import TransactionForm

transactions_bp = Blueprint("transactions", __name__, url_prefix="/transactions")


def _normalize_offline_transaction(item):
    """Validate and normalize one locally queued transaction before saving it."""
    if not isinstance(item, dict):
        return None, "invalid transaction data"

    raw_customer_id = item.get("customer_id")
    if isinstance(raw_customer_id, bool) or not str(raw_customer_id or "").isdigit():
        return None, "choose a valid customer"
    customer_id = int(raw_customer_id)
    if customer_id < 1:
        return None, "choose a valid customer"

    description = item.get("item_description")
    if not isinstance(description, str) or not description.strip():
        return None, "enter an item description"
    description = description.strip()
    if len(description) > 150:
        return None, "item description must be 150 characters or fewer"

    raw_amount = item.get("amount")
    if isinstance(raw_amount, bool):
        return None, "enter a valid amount"
    try:
        amount = Decimal(str(raw_amount))
    except (InvalidOperation, ValueError):
        return None, "enter a valid amount"
    if not amount.is_finite() or amount < Decimal("0.01"):
        return None, "amount must be at least KES 0.01"
    if amount > Decimal("99999999.99") or amount.as_tuple().exponent < -2:
        return None, "amount must be no more than KES 99,999,999.99 with up to 2 decimal places"

    return {
        "customer_id": customer_id,
        "item_description": description,
        "amount": amount,
    }, None


def _populate_customer_choices(form):
    customers = Customer.query.filter_by(retailer_id=current_user.retailer_id).order_by(
        Customer.full_name
    ).all()
    form.customer_id.choices = [("", "Choose a customer")] + [
        (c.customer_id, f"{c.full_name} ({c.phone_number})") for c in customers
    ]


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
    customers_available = len(form.customer_id.choices) > 1

    if customers_available and form.validate_on_submit():
        _apply_transaction(
            current_user.retailer_id,
            form.customer_id.data,
            form.item_description.data,
            form.amount.data,
        )
        flash("Credit sale recorded and ledger updated.", "success")
        return redirect(url_for("transactions.list_transactions"))

    return render_template(
        "transactions/form.html", form=form, customers_available=customers_available
    )


# ---------------------------------------------------------------------------
# Offline-first support: the browser caches transactions recorded while
# offline in IndexedDB (see static/js/offline-db.js) and replays them here
# once connectivity returns (see static/js/sync.js).
# ---------------------------------------------------------------------------
@transactions_bp.route("/api/sync", methods=["POST"])
@login_required
def sync_offline_transactions():
    payload = request.get_json(silent=True)
    if not isinstance(payload, dict):
        return jsonify({"error": "A JSON object is required."}), 400

    pending = payload.get("transactions", [])
    if not isinstance(pending, list):
        return jsonify({"error": "Transactions must be provided as a list."}), 400

    results = []
    for item in pending:
        client_id = item.get("client_id") if isinstance(item, dict) else None
        transaction, error = _normalize_offline_transaction(item)
        if error:
            results.append({"client_id": client_id, "status": "failed", "reason": error})
            continue

        customer = Customer.query.filter_by(
            customer_id=transaction["customer_id"], retailer_id=current_user.retailer_id
        ).first()
        if not customer:
            results.append(
                {"client_id": client_id, "status": "failed", "reason": "unknown customer"}
            )
            continue

        try:
            txn = _apply_transaction(
                current_user.retailer_id,
                customer.customer_id,
                transaction["item_description"],
                transaction["amount"],
                sync_status="synced",
            )
        except SQLAlchemyError:
            db.session.rollback()
            results.append(
                {"client_id": client_id, "status": "failed", "reason": "could not save transaction"}
            )
            continue

        results.append(
            {"client_id": client_id, "status": "synced", "server_id": txn.transaction_id}
        )

    return jsonify({"results": results, "synced_at": datetime.utcnow().isoformat()})
