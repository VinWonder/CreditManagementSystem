from flask import Blueprint, render_template, redirect, url_for, flash, request, jsonify
from flask_login import login_required, current_user
from datetime import datetime
from decimal import Decimal, InvalidOperation
from sqlalchemy.exc import SQLAlchemyError
from sqlalchemy import or_, func

from app import listing
from app.extensions import db
from app.models import Customer, CreditTransaction, Ledger, SaleItem
from app.transactions.forms import (
    ITEM_SIZE_CHOICES,
    ITEM_SIZES_INCHES,
    MAX_ITEM_QUANTITY,
    MAX_SALE_ITEMS,
    TransactionForm,
    format_item_description,
)
from app.transactions.items import summarize_items

transactions_bp = Blueprint("transactions", __name__, url_prefix="/transactions")


def _normalize_offline_item(item):
    """Validate one product from a locally queued sale.

    Returns ((description, quantity, unit_price), error). Sales queued before
    quantities existed send only `amount`, which is the price of one.
    """
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

    raw_quantity = item.get("quantity", 1)
    if isinstance(raw_quantity, bool) or not str(raw_quantity).strip().isdigit():
        return None, "enter a whole-number quantity"
    quantity = int(str(raw_quantity).strip())
    if not 1 <= quantity <= MAX_ITEM_QUANTITY:
        return None, f"quantity must be 1 to {MAX_ITEM_QUANTITY:,}"

    raw_amount = item.get("unit_price", item.get("amount"))
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
    return (description, quantity, amount), None


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
    total = sum((quantity * price for _description, quantity, price in items), Decimal("0"))
    if total > Decimal("99999999.99"):
        return None, "the sale total must be KES 99,999,999.99 or less"

    return {
        "customer_id": customer_id,
        "items": items,
        "item_description": summarize_items(
            [f"{d} ×{q}" if q > 1 else d for d, q, _price in items]
        ),
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
    """Save one sale of one or more (description, quantity, unit_price) items.

    Shared by the normal form AND the offline-sync API.
    """
    rows = [
        SaleItem(
            position=position,
            description=description,
            quantity=quantity,
            unit_price=Decimal(unit_price),
            amount=Decimal(quantity) * Decimal(unit_price),
        )
        for position, (description, quantity, unit_price) in enumerate(items)
    ]
    txn = CreditTransaction(
        retailer_id=retailer_id,
        customer_id=customer_id,
        item_description=summarize_items([row.label for row in rows]),
        amount=sum((row.amount for row in rows), Decimal("0")),
        sync_status=sync_status,
        items=rows,
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
    search = request.args.get("q", "").strip()[:100]
    sync_status = request.args.get("status", "all")
    print_mode = request.args.get("print") == "1"
    if sync_status not in {"all", "synced", "pending"}:
        sync_status = "all"
    period = listing.read_period()
    day = listing.parse_day(request.args.get("day"))

    shop_sales = CreditTransaction.query.filter_by(retailer_id=current_user.retailer_id)
    retailer_transactions = shop_sales
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
    retailer_transactions = listing.filter_by_time(
        retailer_transactions, CreditTransaction.transaction_date, period, day
    )

    filtered_count = retailer_transactions.count()
    ordered_transactions = retailer_transactions.order_by(
        CreditTransaction.transaction_date.desc(),
        CreditTransaction.transaction_id.desc(),
    )
    if print_mode:
        return render_template(
            "transactions/list.html",
            print_mode=True,
            transactions=ordered_transactions.all(),
            filtered_count=filtered_count,
            filtered_amount=retailer_transactions.with_entities(
                func.coalesce(func.sum(CreditTransaction.amount), 0)
            ).scalar(),
            search=search,
            sync_status=sync_status,
        )

    batch = listing.take_batch(ordered_transactions, filtered_count)
    when = lambda sale: sale.transaction_date
    days = {listing.nairobi_day(sale.transaction_date) for sale in batch.rows if sale.transaction_date}
    groups = listing.group_by_day(
        batch.rows,
        when,
        listing.day_totals(retailer_transactions, CreditTransaction.transaction_date, CreditTransaction.amount, days),
    )
    if listing.is_partial():
        return render_template("transactions/_daybook.html", groups=groups, batch=batch)

    return render_template(
        "transactions/list.html",
        print_mode=False,
        groups=groups,
        batch=batch,
        total_count=shop_sales.count(),
        total_amount=shop_sales.with_entities(
            func.coalesce(func.sum(CreditTransaction.amount), 0)
        ).scalar(),
        pending_count=shop_sales.filter_by(sync_status="pending").count(),
        filtered_count=filtered_count,
        search=search,
        sync_status=sync_status,
        period=period,
        periods=listing.PERIODS,
        day=day,
        day_label=listing.day_label(day) if day else "",
        pulse=listing.pulse(shop_sales, CreditTransaction.transaction_date, CreditTransaction.amount),
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
