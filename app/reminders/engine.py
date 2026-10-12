"""
Ageing analysis + automated reminder trigger engine.

For every retailer, every active ReminderRule is matched against customer
Ledgers whose days_overdue has crossed the rule's trigger threshold, a
message is rendered from the template, and dispatched via dispatch_service.
This is run automatically once a day by APScheduler (see scheduler_jobs.py)
and can also be triggered manually from the reminders UI ("Run Now").
"""
from app.extensions import db
from app.models import Retailer, ReminderRule, Ledger, Customer
from app.reminders.dispatch_service import dispatch_reminder


def render_template_message(template, customer, retailer, ledger):
    return (
        template.replace("{customer_name}", customer.full_name)
        .replace("{business_name}", retailer.business_name)
        .replace("{balance}", f"KES {float(ledger.outstanding_balance):,.2f}")
        .replace("{days_overdue}", str(ledger.days_overdue))
    )


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

            for rule in rules:
                if ledger.days_overdue >= rule.trigger_days_overdue:
                    message = render_template_message(
                        rule.message_template, customer, retailer, ledger
                    )
                    success, _info = dispatch_reminder(customer, rule, message)
                    if success:
                        summary["sent"] += 1
                    else:
                        summary["failed"] += 1

        db.session.commit()

    return summary
