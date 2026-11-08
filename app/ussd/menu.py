"""The USSD (*code#) menu: balances and mini-statements on any phone.

Africa's Talking sends every step of a session as one string, the answers so
far joined by "*" (for example "1*2"). `respond()` replays those answers from
the start and returns the next screen: "CON ..." keeps the session open for
another answer, "END ..." shows a last screen and hangs up.

Who sees what is decided by the caller's number, which the mobile network
supplies:

* a customer sees their own balance, mini-statement and how to pay, at every
  shop (with an active subscription) that has them on its books;
* a shop owner sees the shop's figures after entering their USSD PIN.

Shopkeepers are not offered a USSD menu.
"""

from datetime import datetime, timedelta
from decimal import Decimal

from sqlalchemy import func

from app.extensions import db
from app.listing import day_start_utc, nairobi_today
from app.models import (
    BalanceAdjustment,
    CreditTransaction,
    Customer,
    Ledger,
    Payment,
    Retailer,
)
from app.timezones import format_nairobi_datetime

# Most handsets show about 182 characters per USSD screen.
SCREEN_LIMIT = 182
PIN_ATTEMPTS = 5
PIN_LOCK = timedelta(minutes=30)
STATEMENT_LINES = 5
SHOP_STATEMENT_LINES = 6
MAX_SHOPS = 8

SHORT_METHODS = {
    "cash": "Cash",
    "bank": "Bank",
    "agent": "Agent",
    "mpesa_send": "M-Pesa",
    "mpesa_till": "M-Pesa",
    "mpesa_paybill": "M-Pesa",
    "mpesa_stk": "M-Pesa",
    "cheque": "Cheque",
}


# ---- Formatting -----------------------------------------------------------

def con(*lines):
    return _screen("CON", lines)


def end(*lines):
    return _screen("END", lines)


def _screen(kind, lines):
    body = "\n".join(line for line in lines if line is not None)
    room = SCREEN_LIMIT - len(kind) - 1
    if len(body) > room:
        body = body[: room - 1].rstrip() + "…"
    return f"{kind} {body}"


def money(value):
    """KES 1,250 for whole shillings, KES 1,250.50 otherwise."""
    amount = Decimal(value or 0)
    if amount == amount.to_integral_value():
        return f"KES {amount:,.0f}"
    return f"KES {amount:,.2f}"


def amount(value):
    """1,250 or 1,250.50, for statement lines where space is tight."""
    return money(value)[4:]


def short(text, width):
    text = " ".join((text or "").split())
    return text if len(text) <= width else text[: width - 1].rstrip() + "."


def short_name(full_name, width=12):
    """'Amina Hassan' -> 'Amina H'."""
    parts = (full_name or "").split()
    if len(parts) > 1:
        return short(f"{parts[0]} {parts[-1][0]}", width)
    return short(full_name, width)


def day_month(moment):
    return format_nairobi_datetime(moment, "%d/%m") if moment else "--/--"


def plural(count, word):
    return f"{count} {word}{'' if count == 1 else 's'}"


# ---- Session replay -----------------------------------------------------

def answers(text):
    """The caller's answers so far. "0" goes back one step and "00" starts over."""
    steps = []
    for token in (text or "").split("*"):
        token = token.strip()
        if token == "":
            continue
        if token == "00":
            steps = []
        elif token == "0":
            if steps:
                steps.pop()
        else:
            steps.append(token)
    return steps


def respond(phone_number, text, *, only_retailer_id=None):
    """The next screen for this caller. `only_retailer_id` limits what is shown
    to one shop (used by the in-app simulator)."""
    steps = answers(text)

    owner_query = Retailer.query.filter_by(phone_number=phone_number)
    if only_retailer_id is not None:
        owner_query = owner_query.filter_by(retailer_id=only_retailer_id)
    owner = owner_query.first()
    accounts = customer_accounts(phone_number, only_retailer_id)

    if owner and accounts:
        if not steps:
            return con(
                "Duka Credit",
                f"1. My shop: {short(owner.business_name, 20)}",
                "2. My credit accounts",
            )
        choice, rest = steps[0], steps[1:]
        if choice == "1":
            return owner_menu(owner, rest)
        if choice == "2":
            return customer_menu(accounts, rest)
        return end("Invalid choice. Dial again to start over.")
    if owner:
        return owner_menu(owner, steps)
    if accounts:
        return customer_menu(accounts, steps)
    return end(
        "Duka Credit",
        "This number has no account with any shop on Duka Credit.",
    )


# ---- Customers ------------------------------------------------------------

def customer_accounts(phone_number, only_retailer_id=None):
    """This phone's customer records at shops that can use the app, by shop name."""
    query = Customer.query.join(Retailer).filter(Customer.phone_number == phone_number)
    if only_retailer_id is not None:
        query = query.filter(Customer.retailer_id == only_retailer_id)
    accounts = [customer for customer in query.all() if customer.retailer.has_access]
    accounts.sort(key=lambda customer: customer.retailer.business_name.lower())
    return accounts[:MAX_SHOPS]


def customer_menu(accounts, steps):
    if len(accounts) > 1:
        if not steps:
            return con(
                "Duka Credit · choose a shop:",
                *(f"{number}. {short(customer.retailer.business_name, 22)}"
                  for number, customer in enumerate(accounts, start=1)),
            )
        pick = steps[0]
        if not pick.isdigit() or not 1 <= int(pick) <= len(accounts):
            return end("Invalid choice. Dial again to start over.")
        account, steps = accounts[int(pick) - 1], steps[1:]
    else:
        account = accounts[0]

    shop = short(account.retailer.business_name, 24)
    if not steps:
        return con(shop, "1. My balance", "2. Mini-statement", "3. How to pay")
    choice = steps[0]
    if choice == "1":
        return customer_balance(account)
    if choice == "2":
        return customer_statement(account)
    if choice == "3":
        return how_to_pay(account)
    return end("Invalid choice. Dial again to start over.")


def balance_of(customer):
    ledger = customer.ledger
    if ledger is None:
        return Decimal("0"), 0
    return Decimal(ledger.outstanding_balance or 0), ledger.days_overdue or 0


def customer_balance(customer):
    balance, days = balance_of(customer)
    shop = short(customer.retailer.business_name, 24)
    as_at = format_nairobi_datetime(datetime.utcnow(), "%d/%m %H:%M") + " EAT"
    if balance <= 0:
        return end(shop, "You owe nothing. Thank you!" if balance == 0 else f"Credit with shop: {money(-balance)}", f"As at {as_at}")
    return end(
        shop,
        f"You owe {money(balance)}",
        f"Oldest unpaid: {plural(days, 'day')}" if days else "Not overdue",
        f"As at {as_at}",
    )


def statement_entries(customer, limit=STATEMENT_LINES):
    """The customer's latest sales, confirmed payments and corrections, newest first."""
    sales = (
        customer.transactions.order_by(CreditTransaction.transaction_date.desc()).limit(limit).all()
    )
    payments = (
        customer.payments.filter_by(approval_status="approved")
        .order_by(Payment.payment_date.desc())
        .limit(limit)
        .all()
    )
    adjustments = (
        customer.balance_adjustments.order_by(BalanceAdjustment.created_at.desc()).limit(limit).all()
    )
    entries = [(sale.transaction_date, f"{short(sale.item_description, 13)} +{amount(sale.amount)}") for sale in sales]
    entries += [
        (payment.payment_date, f"Paid {SHORT_METHODS.get(payment.payment_method, '')} -{amount(payment.amount_paid)}".replace("  ", " "))
        for payment in payments
    ]
    entries += [
        (
            adjustment.created_at,
            f"Correction {'+' if adjustment.amount_change > 0 else '-'}{amount(abs(adjustment.amount_change))}",
        )
        for adjustment in adjustments
    ]
    entries.sort(key=lambda entry: entry[0] or datetime.min, reverse=True)
    return [f"{day_month(when)} {line}" for when, line in entries[:limit]]


def customer_statement(customer):
    balance, _ = balance_of(customer)
    lines = statement_entries(customer)
    return end(
        f"{short(customer.retailer.business_name, 18)} mini-statement",
        *(lines or ["No entries yet."]),
        f"Balance: {money(balance)}",
    )


def how_to_pay(customer):
    shop = customer.retailer
    account = getattr(shop, "mpesa_account", None)
    lines = [short(shop.business_name, 24)]
    if account is not None and account.active:
        if account.account_type == "till":
            lines.append(f"M-Pesa Buy Goods, Till {account.receiving_number}")
        else:
            lines.append(f"M-Pesa Pay Bill {account.receiving_number}")
            lines.append(f"Account: {customer.phone_number}")
    else:
        lines.append("Pay at the shop.")
    lines.append(f"Shop phone: {shop.phone_number}")
    return end(*lines)


# ---- Shop owners ------------------------------------------------------------

def owner_menu(owner, steps):
    shop = short(owner.business_name, 24)
    if not owner.has_access:
        return end(shop, "Your Duka Credit plan has ended. Renew it in the app to use USSD.")
    if not owner.ussd_pin_hash:
        return end(shop, "Set a USSD PIN first: in the Duka Credit app open Profile > USSD PIN.")
    now = datetime.utcnow()
    if owner.ussd_pin_locked_until and owner.ussd_pin_locked_until > now:
        until = format_nairobi_datetime(owner.ussd_pin_locked_until, "%H:%M")
        return end(shop, f"Too many wrong PINs. Try again after {until} EAT.")
    if not steps:
        return con(f"Duka Credit · {shop}", "Enter your USSD PIN:")

    if not owner.check_ussd_pin(steps[0]):
        owner.ussd_pin_attempts = (owner.ussd_pin_attempts or 0) + 1
        if owner.ussd_pin_attempts >= PIN_ATTEMPTS:
            owner.ussd_pin_attempts = 0
            owner.ussd_pin_locked_until = now + PIN_LOCK
            db.session.commit()
            return end("Wrong PIN. USSD is locked for 30 minutes.")
        left = PIN_ATTEMPTS - owner.ussd_pin_attempts
        db.session.commit()
        return end(f"Wrong PIN. {left} {'try' if left == 1 else 'tries'} left.")
    if owner.ussd_pin_attempts or owner.ussd_pin_locked_until:
        owner.ussd_pin_attempts = 0
        owner.ussd_pin_locked_until = None
        db.session.commit()

    steps = steps[1:]
    if not steps:
        return con(
            shop,
            "1. Today",
            "2. This month",
            "3. Owed & overdue",
            "4. Top debtors",
            "5. Mini-statement",
            "6. Check a customer",
        )
    choice, rest = steps[0], steps[1:]
    if choice == "1":
        return owner_today(owner)
    if choice == "2":
        return owner_month(owner)
    if choice == "3":
        return owner_owed(owner)
    if choice == "4":
        return owner_top_debtors(owner)
    if choice == "5":
        return owner_statement(owner)
    if choice == "6":
        return owner_customer_lookup(owner, rest)
    return end("Invalid choice. Dial again to start over.")


def _sum_since(model, amount_column, date_column, retailer_id, since, **filters):
    query = db.session.query(func.count(), func.coalesce(func.sum(amount_column), 0)).filter(
        model.retailer_id == retailer_id, date_column >= since
    )
    for name, value in filters.items():
        query = query.filter(getattr(model, name) == value)
    count, total = query.one()
    return count, Decimal(total or 0)


def owner_today(owner):
    today = nairobi_today()
    since = day_start_utc(today)
    sales = _sum_since(CreditTransaction, CreditTransaction.amount, CreditTransaction.transaction_date, owner.retailer_id, since)
    paid = _sum_since(Payment, Payment.amount_paid, Payment.payment_date, owner.retailer_id, since, approval_status="approved")
    pending_count, pending_total = (
        db.session.query(func.count(), func.coalesce(func.sum(Payment.amount_paid), 0))
        .filter(Payment.retailer_id == owner.retailer_id, Payment.approval_status == "pending")
        .one()
    )
    lines = [
        f"Today {today.strftime('%d/%m')}",
        f"Credit sales: {sales[0]} · {money(sales[1])}",
        f"Collected: {paid[0]} · {money(paid[1])}",
    ]
    if pending_count:
        lines.append(f"To approve: {pending_count} · {money(pending_total)}")
    return end(*lines)


def owner_month(owner):
    today = nairobi_today()
    since = day_start_utc(today.replace(day=1))
    sales = _sum_since(CreditTransaction, CreditTransaction.amount, CreditTransaction.transaction_date, owner.retailer_id, since)
    paid = _sum_since(Payment, Payment.amount_paid, Payment.payment_date, owner.retailer_id, since, approval_status="approved")
    rate = f"{paid[1] * 100 / sales[1]:.0f}%" if sales[1] else "-"
    return end(
        f"{today.strftime('%B')} so far",
        f"Credit given: {money(sales[1])}",
        f"Collected: {money(paid[1])}",
        f"Collected vs given: {rate}",
    )


def _owing(owner):
    return Ledger.query.filter(Ledger.retailer_id == owner.retailer_id, Ledger.outstanding_balance > 0)


def owner_owed(owner):
    ledgers = _owing(owner).all()
    total = sum((Decimal(ledger.outstanding_balance) for ledger in ledgers), Decimal("0"))
    overdue = [ledger for ledger in ledgers if ledger.days_overdue > 0]
    late = [ledger for ledger in ledgers if ledger.days_overdue > 30]

    def total_of(group):
        return sum((Decimal(ledger.outstanding_balance) for ledger in group), Decimal("0"))

    return end(
        f"Owed: {money(total)} ({plural(len(ledgers), 'customer')})",
        f"Overdue: {money(total_of(overdue))} ({len(overdue)})",
        f"Over 30 days: {money(total_of(late))} ({len(late)})",
    )


def owner_top_debtors(owner):
    ledgers = _owing(owner).order_by(Ledger.outstanding_balance.desc()).limit(5).all()
    if not ledgers:
        return end("No customer owes you anything right now.")
    return end(
        "Top debtors",
        *(
            f"{number}. {short_name(ledger.customer.full_name)} {amount(ledger.outstanding_balance)} ({ledger.days_overdue}d)"
            for number, ledger in enumerate(ledgers, start=1)
        ),
    )


def owner_statement(owner):
    sales = (
        CreditTransaction.query.filter_by(retailer_id=owner.retailer_id)
        .order_by(CreditTransaction.transaction_date.desc())
        .limit(SHOP_STATEMENT_LINES)
        .all()
    )
    payments = (
        Payment.query.filter_by(retailer_id=owner.retailer_id, approval_status="approved")
        .order_by(Payment.payment_date.desc())
        .limit(SHOP_STATEMENT_LINES)
        .all()
    )
    entries = [(sale.transaction_date, f"Sale {short_name(sale.customer.full_name, 10)} {amount(sale.amount)}") for sale in sales]
    entries += [(payment.payment_date, f"Paid {short_name(payment.customer.full_name, 10)} {amount(payment.amount_paid)}") for payment in payments]
    entries.sort(key=lambda entry: entry[0] or datetime.min, reverse=True)
    lines = [f"{day_month(when)} {line}" for when, line in entries[:SHOP_STATEMENT_LINES]]
    return end("Latest activity", *(lines or ["No sales or payments yet."]))


def owner_customer_lookup(owner, steps):
    if not steps:
        return con("Enter the customer's phone number:")
    customer = _find_customer(owner, steps[0])
    if customer is None:
        return end("No customer with that number at your shop.")
    balance, days = balance_of(customer)
    return end(
        short(customer.full_name, 24),
        f"Owes {money(balance)}" + (f" · {days}d" if days and balance > 0 else ""),
        *statement_entries(customer, limit=3),
    )


def _find_customer(owner, typed):
    phone = e164(typed)
    if phone is None:
        return None
    return Customer.query.filter_by(retailer_id=owner.retailer_id, phone_number=phone).first()


def e164(typed):
    """A phone number as stored in the app (+254...), or None if it is not valid."""
    from flask import current_app
    import phonenumbers
    from phonenumbers import NumberParseException, PhoneNumberFormat

    try:
        parsed = phonenumbers.parse((typed or "").strip(), current_app.config.get("DEFAULT_PHONE_REGION", "KE"))
    except NumberParseException:
        return None
    if not phonenumbers.is_valid_number(parsed):
        return None
    return phonenumbers.format_number(parsed, PhoneNumberFormat.E164)
