"""
Ageing analysis + automated reminder trigger engine.

For every retailer, active ReminderRules are matched against customer
Ledgers whose days_overdue has crossed a trigger threshold. The most specific
eligible rule is selected so a single scan does not stack reminders.
This is run automatically once a day by APScheduler (see scheduler_jobs.py)
and can also be triggered manually from the reminders UI ("Run Now").
"""
from app.extensions import db
from app.models import Retailer, ReminderRule, Ledger, Customer
from app.reminders.dispatch_service import dispatch_reminder
from app.transactions.items import customer_item_balances
from app.timezones import format_nairobi_datetime

PAID_MESSAGE_DISCLAIMER = (
    "If you have already paid, please ignore this message. Thank you."
)


def select_matching_rule(rules, days_overdue):
    """Select the most specific eligible threshold to avoid stacked reminders."""
    return max(
        (
            rule
            for rule in rules
            if days_overdue >= rule.trigger_days_overdue
        ),
        key=lambda rule: rule.trigger_days_overdue,
        default=None,
    )


def unpaid_item_details(customer):
    """What the customer still owes, one purchase at a time, for the reminder text.

    "Chips KES 100.00, Soda ×3 KES 300.00 (taken 21 Oct 2026); Sugar (taken 23 Oct 2026)".
    Items already paid are left out; a partly paid item shows what is left.
    """
    purchases = {}
    for balance in customer_item_balances(customer):
        if balance.remaining > 0:
            purchases.setdefault(balance.sale.transaction_id, []).append(balance)
    details = []
    for unpaid in purchases.values():
        sale = unpaid[0].sale
        several = len(sale.items) > 1
        parts = []
        for balance in unpaid:
            if balance.status == "partly paid":
                parts.append(f"{balance.label} (KES {balance.remaining:,.2f} left)")
            elif several or balance.quantity > 1:
                parts.append(f"{balance.label} KES {balance.amount:,.2f}")
            else:
                parts.append(balance.label)
        details.append(
            f"{', '.join(parts)} (taken {format_nairobi_datetime(sale.transaction_date, '%d %b %Y')})"
        )
    return "; ".join(details)


def render_template_message(template, customer, retailer, ledger):
    item_details = unpaid_item_details(customer) or "No item details recorded"

    message = (
        template.replace("{customer_name}", customer.full_name)
        .replace("{customer_shop}", customer.shop_name or "not provided")
        .replace("{business_name}", retailer.business_name)
        .replace("{balance}", f"KES {float(ledger.outstanding_balance):,.2f}")
        .replace("{days_overdue}", str(ledger.days_overdue))
        .replace("{item_details}", item_details)
    )
    if "{item_details}" not in template:
        message = f"{message.rstrip()}\nCredit purchases: {item_details}."
    return message


def render_personal_reminder_message(rule, customer, retailer, ledger):
    """Render the configured rule verbatim and append the required payment note."""
    message = render_template_message(
        rule.message_template, customer, retailer, ledger
    )
    if message.rstrip().endswith(PAID_MESSAGE_DISCLAIMER):
        return message
    return f"{message}\n{PAID_MESSAGE_DISCLAIMER}"


def run_ageing_analysis_and_dispatch(retailer_id=None):
    """Recalculate every ledger's days_overdue, then dispatch matching reminders.

    Returns a summary dict: {"scanned": n, "sent": n, "failed": n}
    """
    summary = {"scanned": 0, "sent": 0, "failed": 0}

    retailer_query = Retailer.query
    if retailer_id:
        retailer_query = retailer_query.filter_by(retailer_id=retailer_id)

    for retailer in retailer_query.all():
        rules = ReminderRule.query.filter_by(retailer_id=retailer.retailer_id, active=True).all()
        if not rules:
            continue

        ledgers = Ledger.query.filter(
            Ledger.retailer_id == retailer.retailer_id, Ledger.outstanding_balance > 0
        ).all()

        for ledger in ledgers:
            ledger.recalculate()
            summary["scanned"] += 1
            customer = Customer.query.get(ledger.customer_id)

            rule = select_matching_rule(rules, ledger.days_overdue)
            if rule:
                message = render_personal_reminder_message(
                    rule, customer, retailer, ledger
                )
                success, _info = dispatch_reminder(customer, rule, message)
                if success:
                    summary["sent"] += 1
                else:
                    summary["failed"] += 1

        db.session.commit()

    return summary
