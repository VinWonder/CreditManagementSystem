"""What each item in a customer's credit sales still owes.

Payments that name items (PaymentAllocation) pay those items first. Every other
shilling paid -- payments that name nothing, including all payments recorded
before items existed -- pays the oldest unpaid items first. The total still owed
across items therefore equals sales minus payments, the same figure the ledger
uses before any manual balance corrections.
"""

from collections import defaultdict
from dataclasses import dataclass
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

    payments = [payment for payment in customer.payments if payment.approval_status in statuses]

    explicit = defaultdict(lambda: ZERO)
    unallocated = ZERO
    for payment in payments:
        named = ZERO
        for allocation in payment.allocations:
            explicit[allocation.sale_item_id] += Decimal(allocation.amount)
            named += Decimal(allocation.amount)
        unallocated += max(Decimal(payment.amount_paid) - named, ZERO)

    for item_id, amount in explicit.items():
        balance = by_item_id.get(item_id)
        if balance is None:
            unallocated += amount
            continue
        applied = min(amount, balance.amount)
        balance.paid += applied
        unallocated += amount - applied

    for balance in balances:
        if unallocated <= ZERO:
            break
        applied = min(unallocated, balance.remaining)
        balance.paid += applied
        unallocated -= applied
    return balances


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
