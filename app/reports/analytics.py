"""Figures for the printable reports.

Every report is built as plain data (a `Report`): headline figures, optional
charts and tables. One template draws any report for screen and print, and the
same tables become the CSV download. Dates are Nairobi calendar days; amounts
are Decimals.
"""

from collections import defaultdict
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from decimal import Decimal

from sqlalchemy import func

from app.extensions import db
from app.listing import day_start_utc, nairobi_day, nairobi_today
from app.models import (
    PAYMENT_METHODS,
    BalanceAdjustment,
    CreditTransaction,
    Customer,
    Ledger,
    MessageDispatchLog,
    Payment,
    SaleItem,
    Shopkeeper,
    StkPushRequest,
)

ZERO = Decimal("0")

# ---- Periods ----------------------------------------------------------------

PERIOD_CHOICES = {
    "today": "Today",
    "7d": "Last 7 days",
    "30d": "Last 30 days",
    "month": "This month",
    "last_month": "Last month",
    "90d": "Last 90 days",
    "year": "This year",
    "custom": "Custom dates",
}
DEFAULT_PERIOD = "30d"
MAX_PERIOD_DAYS = 3 * 366


@dataclass
class Period:
    key: str
    start: date
    end: date  # inclusive

    @property
    def label(self):
        if self.start == self.end:
            return self.start.strftime("%d %b %Y")
        return f"{self.start.strftime('%d %b %Y')} – {self.end.strftime('%d %b %Y')}"

    @property
    def name(self):
        return PERIOD_CHOICES.get(self.key, "Custom dates")

    @property
    def start_utc(self):
        return day_start_utc(self.start)

    @property
    def end_utc(self):
        """Exclusive: the moment the day after `end` begins."""
        return day_start_utc(self.end + timedelta(days=1))

    @property
    def days(self):
        return (self.end - self.start).days + 1

    def within(self, query, column):
        return query.filter(column >= self.start_utc, column < self.end_utc)


def read_period(args, today=None):
    today = today or nairobi_today()
    key = args.get("period", DEFAULT_PERIOD)
    if key not in PERIOD_CHOICES:
        key = DEFAULT_PERIOD
    if key == "today":
        return Period(key, today, today)
    if key == "7d":
        return Period(key, today - timedelta(days=6), today)
    if key == "30d":
        return Period(key, today - timedelta(days=29), today)
    if key == "90d":
        return Period(key, today - timedelta(days=89), today)
    if key == "month":
        return Period(key, today.replace(day=1), today)
    if key == "last_month":
        end = today.replace(day=1) - timedelta(days=1)
        return Period(key, end.replace(day=1), end)
    if key == "year":
        return Period(key, today.replace(month=1, day=1), today)
    start = _parse(args.get("from")) or today - timedelta(days=29)
    end = _parse(args.get("to")) or today
    end = min(end, today)
    if start > end:
        start, end = end, start
    start = max(start, end - timedelta(days=MAX_PERIOD_DAYS))
    return Period("custom", start, end)


def _parse(value):
    try:
        return date.fromisoformat((value or "")[:10])
    except ValueError:
        return None


# ---- Report structure -----------------------------------------------------------

@dataclass
class Column:
    label: str
    kind: str = "text"  # text | money | int | pct | date | datetime


@dataclass
class Table:
    title: str
    columns: list
    rows: list
    totals: list = None
    caption: str = ""
    empty: str = "Nothing in this period."


@dataclass
class Metric:
    label: str
    value: object
    kind: str = "money"
    note: str = ""
    tone: str = ""  # "", good, watch, alert, accent


@dataclass
class Report:
    key: str
    title: str
    subtitle: str = ""
    metrics: list = field(default_factory=list)
    trend: dict = None  # {"labels", "series": [{"name", "tone", "points"}], "peak"}
    breakdowns: list = field(default_factory=list)  # [{"title", "rows": [{"label","value","share","count"}]}]
    tables: list = field(default_factory=list)
    notes: list = field(default_factory=list)
    uses_period: bool = True


@dataclass(frozen=True)
class ReportType:
    key: str
    title: str
    description: str
    icon: str
    admin_only: bool = False
    uses_period: bool = True
    needs_customer: bool = False


REPORT_TYPES = (
    ReportType("summary", "Business summary", "Credit given, money collected, collection rate, trends and top debtors.", "bi-graph-up-arrow"),
    ReportType("sales", "Credit sales", "Every credit sale in the period, with the best-selling items.", "bi-cart-check"),
    ReportType("payments", "Collections", "Confirmed payments by day and by how customers paid.", "bi-cash-coin"),
    ReportType("receivables", "Balances & ageing", "Who owes what today, grouped by how long it has been owed.", "bi-hourglass-split", uses_period=False),
    ReportType("customers", "Customer accounts", "Every customer with credit taken, paid and still owed.", "bi-people", uses_period=False),
    ReportType("statement", "Customer statement", "One customer's sales and payments with a running balance.", "bi-file-earmark-text", needs_customer=True),
    ReportType("approvals", "Payment approvals", "Shopkeeper payments: approved, rejected and still waiting.", "bi-patch-check", admin_only=True),
    ReportType("staff", "Shopkeeper activity", "Payments and M-Pesa prompts each shopkeeper handled.", "bi-person-badge", admin_only=True),
    ReportType("adjustments", "Balance corrections", "Every manual balance correction and its reason.", "bi-sliders", admin_only=True),
    ReportType("reminders", "Reminder messages", "SMS and WhatsApp reminders sent and failed.", "bi-bell", admin_only=True),
)
REPORTS_BY_KEY = {report.key: report for report in REPORT_TYPES}


def reports_for(user):
    return [report for report in REPORT_TYPES if user.is_admin or not report.admin_only]


# ---- Shared figures ---------------------------------------------------------------

def _total(query, column):
    return Decimal(query.with_entities(func.coalesce(func.sum(column), 0)).scalar() or 0)


def sales_in(retailer_id, period):
    return period.within(
        CreditTransaction.query.filter(CreditTransaction.retailer_id == retailer_id),
        CreditTransaction.transaction_date,
    )


def collections_in(retailer_id, period):
    return period.within(
        Payment.query.filter(Payment.retailer_id == retailer_id, Payment.approval_status == "approved"),
        Payment.payment_date,
    )


def owing_ledgers(retailer_id):
    return (
        Ledger.query.filter(Ledger.retailer_id == retailer_id, Ledger.outstanding_balance > 0)
        .order_by(Ledger.outstanding_balance.desc())
        .all()
    )


def pct(part, whole):
    return float(Decimal(part) * 100 / Decimal(whole)) if whole else None


AGE_BANDS = (
    ("Current", 0, 0),
    ("1–7 days", 1, 7),
    ("8–30 days", 8, 30),
    ("31–60 days", 31, 60),
    ("Over 60 days", 61, 10**6),
)


def ageing(ledgers):
    total = sum((Decimal(ledger.outstanding_balance) for ledger in ledgers), ZERO)
    rows = []
    for label, low, high in AGE_BANDS:
        members = [ledger for ledger in ledgers if low <= (ledger.days_overdue or 0) <= high]
        amount = sum((Decimal(ledger.outstanding_balance) for ledger in members), ZERO)
        rows.append({"label": label, "value": amount, "count": len(members), "share": pct(amount, total) or 0})
    return rows


def trend(retailer_id, period):
    """Credit given and money collected per day (or week/month for long periods)."""
    if period.days <= 31:
        step, label = "day", lambda day: day.strftime("%d %b")
    elif period.days <= 120:
        step, label = "week", lambda day: "Wk " + day.strftime("%d %b")
    else:
        step, label = "month", lambda day: day.strftime("%b %Y")

    def bucket(day):
        if step == "day":
            return day
        if step == "week":
            return max(period.start, day - timedelta(days=day.weekday()))
        return day.replace(day=1) if day.replace(day=1) >= period.start else period.start

    keys = []
    cursor = period.start
    while cursor <= period.end:
        key = bucket(cursor)
        if not keys or keys[-1] != key:
            keys.append(key)
        cursor += timedelta(days=1)

    given, collected = defaultdict(lambda: ZERO), defaultdict(lambda: ZERO)
    for stamp, amount in sales_in(retailer_id, period).with_entities(
        CreditTransaction.transaction_date, CreditTransaction.amount
    ):
        given[bucket(nairobi_day(stamp))] += Decimal(amount or 0)
    for stamp, amount in collections_in(retailer_id, period).with_entities(Payment.payment_date, Payment.amount_paid):
        collected[bucket(nairobi_day(stamp))] += Decimal(amount or 0)

    given_values = [given[key] for key in keys]
    collected_values = [collected[key] for key in keys]
    peak = max(given_values + collected_values + [ZERO])
    return {
        "step": step,
        "labels": [label(key) for key in keys],
        "series": [
            {"name": "Credit given", "tone": "given", "points": given_values},
            {"name": "Collected", "tone": "collected", "points": collected_values},
        ],
        "peak": peak,
    }


def method_mix(payments_query):
    rows = (
        payments_query.with_entities(Payment.payment_method, func.count(), func.coalesce(func.sum(Payment.amount_paid), 0))
        .group_by(Payment.payment_method)
        .all()
    )
    total = sum((Decimal(amount or 0) for _, _, amount in rows), ZERO)
    mix = [
        {
            "label": PAYMENT_METHODS.get(method, "Not recorded"),
            "count": count,
            "value": Decimal(amount or 0),
            "share": pct(amount or 0, total) or 0,
        }
        for method, count, amount in rows
    ]
    mix.sort(key=lambda row: row["value"], reverse=True)
    return mix


def recorded_by(payment):
    if payment.submitted_by_shopkeeper is not None:
        return payment.submitted_by_shopkeeper.full_name
    return "Shop admin"


# ---- Reports ----------------------------------------------------------------------

def build(report_type, user, period, customer=None):
    builder = {
        "summary": summary_report,
        "sales": sales_report,
        "payments": payments_report,
        "receivables": receivables_report,
        "customers": customers_report,
        "statement": statement_report,
        "approvals": approvals_report,
        "staff": staff_report,
        "adjustments": adjustments_report,
        "reminders": reminders_report,
    }[report_type.key]
    if report_type.needs_customer:
        report = builder(user, period, customer)
    else:
        report = builder(user, period)
    report.uses_period = report_type.uses_period
    return report


def summary_report(user, period):
    retailer_id = user.retailer_id
    sales = sales_in(retailer_id, period)
    payments = collections_in(retailer_id, period)
    given, sale_count = _total(sales, CreditTransaction.amount), sales.count()
    collected, payment_count = _total(payments, Payment.amount_paid), payments.count()
    ledgers = owing_ledgers(retailer_id)
    outstanding = sum((Decimal(ledger.outstanding_balance) for ledger in ledgers), ZERO)
    overdue = [ledger for ledger in ledgers if ledger.days_overdue > 0]
    overdue_total = sum((Decimal(ledger.outstanding_balance) for ledger in overdue), ZERO)
    weighted_days = (
        sum((Decimal(ledger.outstanding_balance) * ledger.days_overdue for ledger in ledgers), ZERO) / outstanding
        if outstanding else ZERO
    )
    new_customers = period.within(
        Customer.query.filter(Customer.retailer_id == retailer_id), Customer.created_at
    ).count()
    rate = pct(collected, given)

    report = Report("summary", "Business summary", f"{period.name} · {period.label}")
    report.metrics = [
        Metric("Credit given", given, note=f"{sale_count} {'sale' if sale_count == 1 else 'sales'}", tone="accent"),
        Metric("Collected", collected, note=f"{payment_count} confirmed {'payment' if payment_count == 1 else 'payments'}", tone="good"),
        Metric("Collected vs given", rate, kind="pct", note="Money in for every shilling given on credit",
               tone="good" if rate is not None and rate >= 80 else "watch"),
        Metric("Book grew by" if given >= collected else "Book shrank by", abs(given - collected), note="Credit given minus collected"),
        Metric("Owed today", outstanding, note=f"{len(ledgers)} {'customer' if len(ledgers) == 1 else 'customers'} owing"),
        Metric("Overdue today", overdue_total, note=f"{len(overdue)} overdue · {pct(overdue_total, outstanding) or 0:.0f}% of the book",
               tone="alert" if overdue else "good"),
        Metric("Average age of debt", int(weighted_days), kind="days", note="Weighted by amount owed"),
        Metric("Average sale", (given / sale_count) if sale_count else ZERO, note=f"{new_customers} new {'customer' if new_customers == 1 else 'customers'}"),
    ]
    if user.is_admin:
        pending = Payment.query.filter_by(retailer_id=retailer_id, approval_status="pending")
        pending_count, pending_total = pending.count(), _total(pending, Payment.amount_paid)
        report.metrics.append(
            Metric("Waiting for approval", pending_total, note=f"{pending_count} shopkeeper {'payment' if pending_count == 1 else 'payments'}",
                   tone="watch" if pending_count else "")
        )
    report.trend = trend(retailer_id, period)
    report.breakdowns = [
        {"title": "Where the money owed sits", "rows": ageing(ledgers), "count_label": "accounts"},
        {"title": "How customers paid", "rows": method_mix(payments), "count_label": "payments"},
    ]

    top = ledgers[:10]
    report.tables.append(Table(
        "Top debtors today",
        [Column("Customer"), Column("Phone"), Column("Days overdue", "int"), Column("Owes", "money")],
        [[ledger.customer.full_name, ledger.customer.phone_number, ledger.days_overdue, ledger.outstanding_balance] for ledger in top],
        empty="No customer owes anything.",
    ))
    best = (
        payments.join(Customer)
        .with_entities(Customer.full_name, func.count(), func.coalesce(func.sum(Payment.amount_paid), 0))
        .group_by(Customer.customer_id, Customer.full_name)
        .order_by(func.sum(Payment.amount_paid).desc())
        .limit(10)
        .all()
    )
    report.tables.append(Table(
        "Best payers in the period",
        [Column("Customer"), Column("Payments", "int"), Column("Paid", "money")],
        [[name, count, Decimal(amount or 0)] for name, count, amount in best],
        empty="No confirmed payments in this period.",
    ))
    return report


def sales_report(user, period):
    sales = sales_in(user.retailer_id, period)
    rows = sales.order_by(CreditTransaction.transaction_date.desc()).all()
    total = sum((Decimal(sale.amount) for sale in rows), ZERO)
    buyers = len({sale.customer_id for sale in rows})

    report = Report("sales", "Credit sales", f"{period.name} · {period.label}")
    report.metrics = [
        Metric("Credit given", total, tone="accent"),
        Metric("Sales", len(rows), kind="int"),
        Metric("Average sale", total / len(rows) if rows else ZERO),
        Metric("Customers who bought", buyers, kind="int"),
    ]
    report.trend = trend(user.retailer_id, period)
    report.trend["series"] = report.trend["series"][:1]
    report.trend["peak"] = max(report.trend["series"][0]["points"] + [ZERO])

    items = (
        db.session.query(
            func.lower(SaleItem.description),
            func.min(SaleItem.description),
            func.coalesce(func.sum(SaleItem.quantity), 0),
            func.coalesce(func.sum(SaleItem.amount), 0),
        )
        .join(CreditTransaction, SaleItem.transaction_id == CreditTransaction.transaction_id)
        .filter(
            CreditTransaction.retailer_id == user.retailer_id,
            CreditTransaction.transaction_date >= period.start_utc,
            CreditTransaction.transaction_date < period.end_utc,
        )
        .group_by(func.lower(SaleItem.description))
        .order_by(func.sum(SaleItem.amount).desc())
        .limit(8)
        .all()
    )
    item_total = sum((Decimal(amount or 0) for *_, amount in items), ZERO)
    if items:
        report.breakdowns = [{
            "title": "Top items by value",
            "rows": [
                {"label": name, "count": int(quantity or 0), "value": Decimal(amount or 0), "share": pct(amount or 0, item_total) or 0}
                for _, name, quantity, amount in items
            ],
            "count_label": "sold",
        }]
    report.tables.append(Table(
        "All credit sales",
        [Column("Date", "datetime"), Column("Customer"), Column("Items"), Column("Amount", "money")],
        [[sale.transaction_date, sale.customer.full_name, _items_text(sale), sale.amount] for sale in rows],
        totals=["Total", "", f"{len(rows)} sales", total],
    ))
    return report


def _items_text(sale):
    if sale.items:
        return ", ".join(item.label for item in sale.items)
    return sale.item_description


def payments_report(user, period):
    payments = collections_in(user.retailer_id, period)
    rows = payments.order_by(Payment.payment_date.desc()).all()
    total = sum((Decimal(payment.amount_paid) for payment in rows), ZERO)
    payers = len({payment.customer_id for payment in rows})

    report = Report("payments", "Collections", f"{period.name} · {period.label} · confirmed payments only")
    report.metrics = [
        Metric("Collected", total, tone="good"),
        Metric("Payments", len(rows), kind="int"),
        Metric("Average payment", total / len(rows) if rows else ZERO),
        Metric("Customers who paid", payers, kind="int"),
    ]
    report.trend = trend(user.retailer_id, period)
    report.trend["series"] = report.trend["series"][1:]
    report.trend["peak"] = max(report.trend["series"][0]["points"] + [ZERO])
    report.breakdowns = [{"title": "How customers paid", "rows": method_mix(payments), "count_label": "payments"}]
    report.tables.append(Table(
        "All confirmed payments",
        [Column("Date", "datetime"), Column("Customer"), Column("Method"), Column("Reference"), Column("Recorded by"), Column("Amount", "money")],
        [
            [payment.payment_date, payment.customer.full_name, payment.payment_method_label,
             payment.payment_reference or "", recorded_by(payment), payment.amount_paid]
            for payment in rows
        ],
        totals=["Total", "", "", "", f"{len(rows)} payments", total],
    ))
    return report


def receivables_report(user, period):
    ledgers = owing_ledgers(user.retailer_id)
    ledgers.sort(key=lambda ledger: (ledger.days_overdue, ledger.outstanding_balance), reverse=True)
    total = sum((Decimal(ledger.outstanding_balance) for ledger in ledgers), ZERO)
    overdue = [ledger for ledger in ledgers if ledger.days_overdue > 0]
    overdue_total = sum((Decimal(ledger.outstanding_balance) for ledger in overdue), ZERO)
    late_total = sum((Decimal(ledger.outstanding_balance) for ledger in ledgers if ledger.days_overdue > 30), ZERO)

    report = Report("receivables", "Balances & ageing", "As at " + nairobi_today().strftime("%d %b %Y"))
    report.metrics = [
        Metric("Owed", total, note=f"{len(ledgers)} {'customer' if len(ledgers) == 1 else 'customers'}", tone="accent"),
        Metric("Overdue", overdue_total, note=f"{len(overdue)} {'account' if len(overdue) == 1 else 'accounts'}", tone="alert" if overdue else "good"),
        Metric("Over 30 days", late_total, note=f"{pct(late_total, total) or 0:.0f}% of the book", tone="alert" if late_total else ""),
        Metric("Largest balance", ledgers and max(Decimal(ledger.outstanding_balance) for ledger in ledgers) or ZERO),
    ]
    report.breakdowns = [{"title": "Where the money owed sits", "rows": ageing(ledgers), "count_label": "accounts"}]
    report.tables.append(Table(
        "Customers who owe",
        [Column("Customer"), Column("Phone"), Column("Age band"), Column("Days overdue", "int"), Column("Owes", "money")],
        [
            [ledger.customer.full_name, ledger.customer.phone_number, _band_of(ledger.days_overdue),
             ledger.days_overdue, ledger.outstanding_balance]
            for ledger in ledgers
        ],
        totals=["Total", "", "", f"{len(ledgers)} accounts", total],
        empty="No customer owes anything.",
    ))
    return report


def _band_of(days):
    for label, low, high in AGE_BANDS:
        if low <= (days or 0) <= high:
            return label
    return ""


def customers_report(user, period):
    retailer_id = user.retailer_id
    customers = Customer.query.filter_by(retailer_id=retailer_id).order_by(Customer.full_name.asc()).all()
    given = dict(
        db.session.query(CreditTransaction.customer_id, func.coalesce(func.sum(CreditTransaction.amount), 0))
        .filter(CreditTransaction.retailer_id == retailer_id)
        .group_by(CreditTransaction.customer_id)
        .all()
    )
    paid = dict(
        db.session.query(Payment.customer_id, func.coalesce(func.sum(Payment.amount_paid), 0))
        .filter(Payment.retailer_id == retailer_id, Payment.approval_status == "approved")
        .group_by(Payment.customer_id)
        .all()
    )
    last_sale = dict(
        db.session.query(CreditTransaction.customer_id, func.max(CreditTransaction.transaction_date))
        .filter(CreditTransaction.retailer_id == retailer_id)
        .group_by(CreditTransaction.customer_id)
        .all()
    )
    rows = []
    totals = [ZERO, ZERO, ZERO]
    for customer in customers:
        balance = Decimal(customer.ledger.outstanding_balance or 0) if customer.ledger else ZERO
        taken, returned = Decimal(given.get(customer.customer_id, 0)), Decimal(paid.get(customer.customer_id, 0))
        totals = [totals[0] + taken, totals[1] + returned, totals[2] + balance]
        rows.append([customer.full_name, customer.phone_number, customer.shop_name or "",
                     last_sale.get(customer.customer_id), taken, returned, balance])
    owing = sum(1 for row in rows if row[-1] > 0)

    report = Report("customers", "Customer accounts", "All time · as at " + nairobi_today().strftime("%d %b %Y"))
    report.metrics = [
        Metric("Customers", len(customers), kind="int", tone="accent"),
        Metric("Owing now", owing, kind="int", note=f"{len(customers) - owing} paid up or no purchases"),
        Metric("Credit given (all time)", totals[0]),
        Metric("Paid back (all time)", totals[1], tone="good"),
    ]
    report.tables.append(Table(
        "Customers",
        [Column("Customer"), Column("Phone"), Column("Business"), Column("Last sale", "date"),
         Column("Credit taken", "money"), Column("Paid", "money"), Column("Owes", "money")],
        rows,
        totals=["Total", "", "", "", *totals],
        empty="No customers yet.",
    ))
    return report


def statement_report(user, period, customer):
    report = Report("statement", "Customer statement", f"{period.name} · {period.label}")
    if customer is None:
        report.notes.append("Choose a customer to see their statement.")
        return report
    report.title = f"Statement · {customer.full_name}"
    report.subtitle = f"{customer.phone_number}{' · ' + customer.shop_name if customer.shop_name else ''} · {period.label}"

    def entries_before(cutoff):
        sales = _total(customer.transactions.filter(CreditTransaction.transaction_date < cutoff), CreditTransaction.amount)
        payments = _total(
            customer.payments.filter(Payment.approval_status == "approved", Payment.payment_date < cutoff), Payment.amount_paid
        )
        adjustments = _total(customer.balance_adjustments.filter(BalanceAdjustment.created_at < cutoff), BalanceAdjustment.amount_change)
        return sales - payments + adjustments

    opening = entries_before(period.start_utc)
    events = []
    for sale in period.within(customer.transactions, CreditTransaction.transaction_date).all():
        events.append((sale.transaction_date, "Credit sale: " + _items_text(sale), Decimal(sale.amount), ZERO))
    for payment in period.within(customer.payments.filter(Payment.approval_status == "approved"), Payment.payment_date).all():
        reference = f" · {payment.payment_reference}" if payment.payment_reference else ""
        events.append((payment.payment_date, f"Payment: {payment.payment_method_label}{reference}", ZERO, Decimal(payment.amount_paid)))
    for adjustment in period.within(customer.balance_adjustments, BalanceAdjustment.created_at).all():
        change = Decimal(adjustment.amount_change)
        events.append((adjustment.created_at, "Correction: " + adjustment.reason,
                       change if change > 0 else ZERO, -change if change < 0 else ZERO))
    events.sort(key=lambda event: event[0] or datetime.min)

    running = opening
    rows = [[period.start_utc, "Opening balance", None, None, opening]]
    charged = received = ZERO
    for when, text, debit, credit in events:
        running += debit - credit
        charged += debit
        received += credit
        rows.append([when, text, debit or None, credit or None, running])

    report.metrics = [
        Metric("Opening balance", opening),
        Metric("Charged", charged, tone="accent"),
        Metric("Paid", received, tone="good"),
        Metric("Closing balance", running, tone="alert" if running > 0 else "good"),
    ]
    report.tables.append(Table(
        "Statement",
        [Column("Date", "datetime"), Column("Details"), Column("Charged", "money"), Column("Paid", "money"), Column("Balance", "money")],
        rows,
        totals=["Closing balance", "", charged, received, running],
    ))
    return report


def approvals_report(user, period):
    payments = period.within(
        Payment.query.filter(Payment.retailer_id == user.retailer_id, Payment.submitted_by_shopkeeper_id.isnot(None)),
        Payment.payment_date,
    )
    rows = payments.order_by(Payment.payment_date.desc()).all()

    def stats(status):
        group = [payment for payment in rows if payment.approval_status == status]
        return len(group), sum((Decimal(payment.amount_paid) for payment in group), ZERO)

    approved, rejected, pending = stats("approved"), stats("rejected"), stats("pending")
    report = Report("approvals", "Payment approvals", f"{period.name} · {period.label} · payments logged by shopkeepers")
    report.metrics = [
        Metric("Approved", approved[1], note=f"{approved[0]} payments", tone="good"),
        Metric("Waiting", pending[1], note=f"{pending[0]} payments", tone="watch" if pending[0] else ""),
        Metric("Rejected", rejected[1], note=f"{rejected[0]} payments", tone="alert" if rejected[0] else ""),
        Metric("Approval rate", pct(approved[0], approved[0] + rejected[0]), kind="pct", note="Of payments decided"),
    ]
    report.tables.append(Table(
        "Shopkeeper payments",
        [Column("Logged", "datetime"), Column("Customer"), Column("Shopkeeper"), Column("Method"), Column("Reference"),
         Column("Status"), Column("Reviewed", "datetime"), Column("Note"), Column("Amount", "money")],
        [
            [payment.payment_date, payment.customer.full_name, recorded_by(payment), payment.payment_method_label,
             payment.payment_reference or "", payment.approval_status.capitalize(), payment.reviewed_at,
             payment.review_note or "", payment.amount_paid]
            for payment in rows
        ],
        empty="No shopkeeper payments in this period.",
    ))
    return report


def staff_report(user, period):
    retailer_id = user.retailer_id
    shopkeepers = Shopkeeper.query.filter_by(retailer_id=retailer_id).order_by(Shopkeeper.full_name.asc()).all()
    payments = period.within(Payment.query.filter(Payment.retailer_id == retailer_id), Payment.payment_date).all()
    prompts = period.within(
        StkPushRequest.query.filter(StkPushRequest.retailer_id == retailer_id), StkPushRequest.created_at
    ).all()

    def line(name, active, owner_id):
        mine = [payment for payment in payments if payment.submitted_by_shopkeeper_id == owner_id]
        my_prompts = [prompt for prompt in prompts if prompt.requested_by_shopkeeper_id == owner_id]

        def money_of(status):
            return sum((Decimal(payment.amount_paid) for payment in mine if payment.approval_status == status), ZERO)

        return [
            name, active, len(mine),
            sum(1 for payment in mine if payment.approval_status == "approved"),
            sum(1 for payment in mine if payment.approval_status == "rejected"),
            sum(1 for payment in mine if payment.approval_status == "pending"),
            len(my_prompts), sum(1 for prompt in my_prompts if prompt.status == "paid"),
            money_of("approved"),
        ]

    rows = [line("Shop admin", "Owner", None)]
    rows += [line(keeper.full_name, "Active" if keeper.active else "Deactivated", keeper.shopkeeper_id) for keeper in shopkeepers]
    staff_rows = rows[1:]
    report = Report("staff", "Shopkeeper activity", f"{period.name} · {period.label}")
    report.metrics = [
        Metric("Shopkeepers", len(shopkeepers), kind="int", note=f"{sum(1 for keeper in shopkeepers if keeper.active)} active", tone="accent"),
        Metric("Payments by shopkeepers", sum(row[2] for row in staff_rows), kind="int"),
        Metric("Collected by shopkeepers", sum((row[-1] for row in staff_rows), ZERO), tone="good"),
        Metric("Rejected", sum(row[4] for row in staff_rows), kind="int", tone="alert" if any(row[4] for row in staff_rows) else ""),
    ]
    report.tables.append(Table(
        "By person",
        [Column("Name"), Column("Status"), Column("Payments logged", "int"), Column("Approved", "int"), Column("Rejected", "int"),
         Column("Waiting", "int"), Column("M-Pesa prompts", "int"), Column("Prompts paid", "int"), Column("Confirmed amount", "money")],
        rows,
    ))
    report.notes.append("Credit sales are not recorded per person, so this report covers payments and M-Pesa prompts.")
    return report


def adjustments_report(user, period):
    adjustments = period.within(
        BalanceAdjustment.query.filter(BalanceAdjustment.retailer_id == user.retailer_id), BalanceAdjustment.created_at
    ).order_by(BalanceAdjustment.created_at.desc()).all()
    raised = sum((Decimal(item.amount_change) for item in adjustments if item.amount_change > 0), ZERO)
    lowered = sum((-Decimal(item.amount_change) for item in adjustments if item.amount_change < 0), ZERO)
    report = Report("adjustments", "Balance corrections", f"{period.name} · {period.label}")
    report.metrics = [
        Metric("Corrections", len(adjustments), kind="int", tone="accent"),
        Metric("Balances raised", raised, tone="watch" if raised else ""),
        Metric("Balances lowered", lowered),
        Metric("Net change", raised - lowered),
    ]
    report.tables.append(Table(
        "Corrections",
        [Column("Date", "datetime"), Column("Customer"), Column("Reason"), Column("Change", "money")],
        [[item.created_at, item.customer.full_name, item.reason, item.amount_change] for item in adjustments],
        totals=["Net change", "", f"{len(adjustments)} corrections", raised - lowered],
        empty="No balance corrections in this period.",
    ))
    return report


def reminders_report(user, period):
    logs = period.within(
        MessageDispatchLog.query.join(Customer).filter(Customer.retailer_id == user.retailer_id), MessageDispatchLog.sent_at
    ).order_by(MessageDispatchLog.sent_at.desc()).all()
    sent = sum(1 for log in logs if log.status == "sent")
    failed = sum(1 for log in logs if log.status == "failed")
    report = Report("reminders", "Reminder messages", f"{period.name} · {period.label}")
    report.metrics = [
        Metric("Messages", len(logs), kind="int", tone="accent"),
        Metric("Sent", sent, kind="int", tone="good"),
        Metric("Failed", failed, kind="int", tone="alert" if failed else ""),
        Metric("Delivered rate", pct(sent, len(logs)), kind="pct"),
    ]
    channels = defaultdict(int)
    for log in logs:
        channels[log.channel] += 1
    if logs:
        report.breakdowns = [{
            "title": "By channel",
            "rows": [
                {"label": "WhatsApp" if channel == "whatsapp" else channel.upper(), "count": count, "value": count,
                 "share": pct(count, len(logs)) or 0}
                for channel, count in sorted(channels.items(), key=lambda pair: -pair[1])
            ],
            "count_only": True,
        }]
    report.tables.append(Table(
        "Messages",
        [Column("Sent", "datetime"), Column("Customer"), Column("Channel"), Column("Status"), Column("Problem")],
        [
            [log.sent_at, log.customer.full_name, "WhatsApp" if log.channel == "whatsapp" else log.channel.upper(),
             log.status.capitalize(), log.failure_reason or ""]
            for log in logs
        ],
        empty="No reminders in this period.",
    ))
    return report
