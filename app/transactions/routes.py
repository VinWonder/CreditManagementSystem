from flask import Blueprint, render_template, redirect, url_for, flash, request, jsonify
from flask_login import login_required, current_user
from datetime import datetime
from decimal import Decimal, InvalidOperation
from math import ceil
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy import or_, func

from app.extensions import db
from app.models import Customer, CreditTransaction, Ledger, SaleItem
from app.transactions.forms import (
    ITEM_SIZE_CHOICES,
    ITEM_SIZES_INCHES,
    MAX_SALE_ITEMS,
    TransactionForm,
    format_item_description,
)
from app.transactions.items import summarize_items

transactions_bp = Blueprint("transactions", __name__, url_prefix="/transactions")


def _normalize_offline_item(item):
    """Validate one product from a locally queued sale. Returns ((description, amount), error)."""
    if not isinstance(item, dict):
        return None, "invalid item"
    description = item.get("item_description")
    if not isinstance(description, str) or not description.strip():
        return None, "enter an item description"
    size_inches = item.get("item_size_inches", "")
    if not isinstance(size_inches, str) or size_inches not in ITEM_SIZES_INCHES | {""}:
        return None, "choose a valid item size"
    description = format_item_description(description.strip(), size_inches)
    if len(description) > 150:
        return None, "item description and size must be 150 characters or fewer"

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
    return (description, amount), None


def _normalize_offline_transaction(item):
    """Validate and normalize one locally queued sale before saving it.

    New devices send {"customer_id", "items": [...]}; sales queued by older
    versions of the page send one item_description / amount at the top level.
    """
    if not isinstance(item, dict):
        return None, "invalid transaction data"

    raw_customer_id = item.get("customer_id")
    if isinstance(raw_customer_id, bool) or not str(raw_customer_id or "").isdigit():
        return None, "choose a valid customer"
    customer_id = int(raw_customer_id)
    if customer_id < 1:
        return None, "choose a valid customer"

    raw_items = item.get("items") if "items" in item else [item]
    if not isinstance(raw_items, list) or not raw_items:
        return None, "add at least one item"
    if len(raw_items) > MAX_SALE_ITEMS:
        return None, f"a sale can have at most {MAX_SALE_ITEMS} items"
    items = []
    for raw_item in raw_items:
        normalized, error = _normalize_offline_item(raw_item)
        if error:
            return None, error
        items.append(normalized)
    total = sum((amount for _description, amount in items), Decimal("0"))
    if total > Decimal("99999999.99"):
        return None, "the sale total must be KES 99,999,999.99 or less"

    return {
        "customer_id": customer_id,
        "items": items,
        "item_description": summarize_items([description for description, _amount in items]),
        "amount": total,
    }, None


def _populate_customer_choices(form):
    customers = Customer.query.filter_by(retailer_id=current_user.retailer_id).order_by(
        Customer.full_name
    ).all()
    form.customer_id.choices = [("", "Choose a customer")] + [
        (c.customer_id, f"{c.full_name} ({c.phone_number})") for c in customers
    ]


def _apply_transaction(retailer_id, customer_id, items, sync_status="synced"):
    """Save one sale of one or more (description, amount) items.

    Shared by the normal form AND the offline-sync API.
    """
    txn = CreditTransaction(
        retailer_id=retailer_id,
        customer_id=customer_id,
        item_description=summarize_items([description for description, _amount in items]),
        amount=sum((Decimal(amount) for _description, amount in items), Decimal("0")),
        sync_status=sync_status,
        items=[
            SaleItem(position=position, description=description, amount=amount)
            for position, (description, amount) in enumerate(items)
        ],
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
    page_size = 25
    search = request.args.get("q", "").strip()[:100]
    sync_status = request.args.get("status", "all")
    print_mode = request.args.get("print") == "1"
    if sync_status not in {"all", "synced", "pending"}:
        sync_status = "all"

    retailer_transactions = CreditTransaction.query.filter_by(
        retailer_id=current_user.retailer_id
    )
    if search:
        retailer_transactions = retailer_transactions.join(Customer).filter(
            or_(
                Customer.full_name.ilike(f"%{search}%"),
                CreditTransaction.item_description.ilike(f"%{search}%"),
            )
        )
    if sync_status != "all":
        retailer_transactions = retailer_transactions.filter(
            CreditTransaction.sync_status == sync_status
        )

    total_count = CreditTransaction.query.filter_by(
        retailer_id=current_user.retailer_id
    ).count()
    total_amount = (
        db.session.query(func.coalesce(func.sum(CreditTransaction.amount), 0))
        .filter(CreditTransaction.retailer_id == current_user.retailer_id)
        .scalar()
    )
    pending_count = CreditTransaction.query.filter_by(
        retailer_id=current_user.retailer_id, sync_status="pending"
    ).count()
    filtered_count = retailer_transactions.count()
    total_pages = ceil(filtered_count / page_size)
    page = request.args.get("page", 1, type=int) or 1
    page = min(max(page, 1), max(total_pages, 1))
    ordered_transactions = retailer_transactions.order_by(
        CreditTransaction.transaction_date.desc(),
        CreditTransaction.transaction_id.desc(),
    )
    if print_mode:
        transactions = ordered_transactions.all()
        filtered_amount = (
            retailer_transactions.with_entities(
                func.coalesce(func.sum(CreditTransaction.amount), 0)
            ).scalar()
        )
    else:
        transactions = (
            ordered_transactions.offset((page - 1) * page_size).limit(page_size).all()
        )
        filtered_amount = 0

    return render_template(
        "transactions/list.html",
        transactions=transactions,
        total_count=total_count,
        total_amount=total_amount,
        pending_count=pending_count,
        filtered_count=filtered_count,
        search=search,
        sync_status=sync_status,
        page=page,
        total_pages=total_pages,
        print_mode=print_mode,
        filtered_amount=filtered_amount,
    )


@transactions_bp.route("/new", methods=["GET", "POST"])
@login_required
def new_transaction():
    form = TransactionForm()
    _populate_customer_choices(form)
    customers_available = len(form.customer_id.choices) > 1

    if customers_available and form.validate_on_submit():
        txn = _apply_transaction(current_user.retailer_id, form.customer_id.data, form.sale_items)
        item_count = len(txn.items)
        flash(
            f"Credit sale of {item_count} item{'s' if item_count != 1 else ''} "
            f"(KES {txn.amount:,.2f}) recorded and ledger updated.",
            "success",
        )
        return redirect(url_for("transactions.list_transactions"))

    return render_template(
        "transactions/form.html",
        form=form,
        customers_available=customers_available,
        size_choices=[("", "No size")] + ITEM_SIZE_CHOICES,
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
                transaction["items"],
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
