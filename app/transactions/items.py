"""What each item in a customer's credit sales still owes.

Payments that name items (PaymentAllocation) pay those items first. Every other
shilling paid -- payments that name nothing, including all payments recorded
before items existed -- pays the oldest unpaid items first. The total still owed
across items therefore equals sales minus payments, the same figure the ledger
uses before any manual balance corrections.
"""

from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
from decimal import Decimal

from app.models import CreditTransaction, PaymentAllocation, SaleItem

ZERO = Decimal("0.00")
SUMMARY_LIMIT = 150


@dataclass
class ItemBalance:
    sale: CreditTransaction
    item: SaleItem | None  # None for an old sale saved without item rows
    description: str
    amount: Decimal
    paid: Decimal = ZERO

    @property
    def item_id(self):
        return self.item.sale_item_id if self.item is not None else None

    @property
    def remaining(self):
        return max(self.amount - self.paid, ZERO)

    @property
    def status(self):
        if self.remaining == ZERO:
            return "paid"
        return "partly paid" if self.paid > ZERO else "unpaid"


def summarize_items(descriptions):
    """'Chips, Soda' for the sale's one-line description, kept within 150 characters."""
    summary = ", ".join(description.strip() for description in descriptions if description.strip())
    if len(summary) > SUMMARY_LIMIT:
        summary = summary[: SUMMARY_LIMIT - 1].rstrip(", ") + "…"
    return summary


def customer_item_balances(customer, include_pending=False):
    """Every item the customer bought on credit, oldest first, with what is still owed.

    include_pending also counts payments waiting for admin approval, so the same
    item cannot be offered for payment twice while one payment is under review.
    """
    statuses = ("approved", "pending") if include_pending else ("approved",)
    balances = []
    by_item_id = {}
    sales = customer.transactions.order_by(
        CreditTransaction.transaction_date.asc(), CreditTransaction.transaction_id.asc()
    ).all()
    for sale in sales:
        if sale.items:
            for item in sale.items:
                balance = ItemBalance(sale, item, item.description, Decimal(item.amount))
                balances.append(balance)
                by_item_id[item.sale_item_id] = balance
        else:
            balances.append(ItemBalance(sale, None, sale.item_description, Decimal(sale.amount)))

    balances, _paid_by = _settle(customer, statuses, balances, by_item_id)
    return balances


def payment_item_breakdown(customer):
    """For each approved payment, the items it paid: {payment_id: [(ItemBalance, amount)]}.

    Uses the same rules as customer_item_balances, taking payments that name no
    items in the order they were made, so a receipt can say what was paid for.
    """
    balances = []
    by_item_id = {}
    for balance in customer_item_balances(customer):
        balance.paid = ZERO
        balances.append(balance)
        if balance.item_id is not None:
            by_item_id[balance.item_id] = balance
    _balances, paid_by = _settle(customer, ("approved",), balances, by_item_id)
    return paid_by


def _settle(customer, statuses, balances, by_item_id):
    """Apply payments to items: named items first, then the oldest unpaid items.

    Returns the balances and, per payment, which items it paid and how much.
    """
    payments = sorted(
        (payment for payment in customer.payments if payment.approval_status in statuses),
        key=lambda payment: (payment.payment_date or datetime.min, payment.payment_id or 0),
    )
    paid_by = defaultdict(list)
    # Money each payment still has to give after its named items, in payment order.
    leftovers = []
    for payment in payments:
        named = ZERO
        for allocation in payment.allocations:
            amount = Decimal(allocation.amount)
            named += amount
            balance = by_item_id.get(allocation.sale_item_id)
            applied = min(amount, balance.remaining) if balance is not None else ZERO
            if applied > ZERO:
                balance.paid += applied
                paid_by[payment.payment_id].append((balance, applied))
            if amount - applied > ZERO:
                leftovers.append([payment, amount - applied])
        unnamed = max(Decimal(payment.amount_paid) - named, ZERO)
        if unnamed > ZERO:
            leftovers.append([payment, unnamed])

    queue = iter(balance for balance in balances)
    current = next(queue, None)
    for payment, amount in leftovers:
        while amount > ZERO and current is not None:
            if current.remaining <= ZERO:
                current = next(queue, None)
                continue
            applied = min(amount, current.remaining)
            current.paid += applied
            amount -= applied
            paid_by[payment.payment_id].append((current, applied))
    return balances, paid_by


def allocate_payment(payment, customer, sale_item_ids):
    """Record which items a payment is for, filling each in the order chosen.

    Raises ValueError when an item is not this customer's or is already paid, or
    when the payment is larger than what the chosen items still owe.
    """
    if not sale_item_ids:
        return []
    owing = {
        balance.item_id: balance
        for balance in customer_item_balances(customer, include_pending=True)
        if balance.item_id is not None
    }
    chosen = []
    for item_id in sale_item_ids:
        balance = owing.get(item_id)
        if balance is None or balance.remaining <= ZERO:
            raise ValueError("One of the chosen items is not owed by this customer any more.")
        if balance not in chosen:
            chosen.append(balance)
    total_owed = sum((balance.remaining for balance in chosen), ZERO)
    left = Decimal(payment.amount_paid)
    if left > total_owed:
        raise ValueError(
            f"The chosen items only have KES {total_owed:,.2f} left to pay. "
            "Lower the amount or choose more items."
        )
    allocations = []
    for balance in chosen:
        if left <= ZERO:
            break
        applied = min(left, balance.remaining)
        allocations.append(PaymentAllocation(payment=payment, sale_item_id=balance.item_id, amount=applied))
        left -= applied
    return allocations
