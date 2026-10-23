"""Payment confirmation messages the shop admin sends from their own phone."""

from decimal import Decimal

from app.timezones import format_nairobi_datetime
from app.transactions.items import payment_item_breakdown


def paid_items_text(payment):
    """'Chips KES 100.00, Soda ×2 of 3 KES 200.00 of 300.00' for the items this payment covered."""
    units_paid = {a.sale_item_id: a.quantity for a in payment.allocations if a.quantity}
    parts = []
    for balance, applied in payment_item_breakdown(payment.customer).get(payment.payment_id, []):
        units = units_paid.get(balance.item_id)
        if units and units < balance.quantity:
            name = f"{balance.description} ×{units} of {balance.quantity}"
        else:
            name = balance.label
        if applied >= balance.amount:
            parts.append(f"{name} KES {applied:,.2f}")
        else:
            parts.append(f"{name} KES {applied:,.2f} of {balance.amount:,.2f}")
    return ", ".join(parts)


def receipt_message(payment):
    """The confirmation text: shop, amount, EAT date and time, method, items and balance."""
    customer = payment.customer
    shop = payment.retailer.business_name
    when = format_nairobi_datetime(payment.payment_date, "%d %b %Y at %H:%M") + " EAT"
    lines = [
        f"Hello {customer.full_name}, {shop} confirms we received your payment of "
        f"KES {Decimal(payment.amount_paid):,.2f} on {when}."
    ]
    method = payment.payment_method_label if payment.payment_method else None
    if method and payment.payment_reference:
        lines.append(f"Paid by {method}, ref {payment.payment_reference}.")
    elif method:
        lines.append(f"Paid by {method}.")
    items = paid_items_text(payment)
    if items:
        lines.append(f"For: {items}.")
    balance = Decimal(customer.ledger.outstanding_balance) if customer.ledger else Decimal("0")
    if balance > 0:
        lines.append(f"Balance still owed: KES {balance:,.2f}.")
    else:
        lines.append("Your account is fully paid.")
    lines.append("Thank you!")
    return "\n".join(lines)
