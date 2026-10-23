"""Shared helpers for the history lists: day groups, day totals, the 14-day
pulse strip, period filters and "show more" paging.

Dates are stored as naive UTC; every day here is a Nairobi calendar day.
"""

from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal

from flask import request

from app.timezones import NAIROBI_TIMEZONE

PAGE_SIZE = 20
MAX_SHOWN = 500
PULSE_DAYS = 14
PERIODS = {
    "all": ("All time", None),
    "today": ("Today", 0),
    "7d": ("7 days", 6),
    "30d": ("30 days", 29),
}


def nairobi_today(now=None):
    now = now or datetime.now(timezone.utc)
    return now.astimezone(NAIROBI_TIMEZONE).date()


def nairobi_day(value):
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(NAIROBI_TIMEZONE).date()


def day_start_utc(day):
    """The naive UTC moment a Nairobi calendar day begins."""
    local = datetime.combine(day, time.min, tzinfo=NAIROBI_TIMEZONE)
    return local.astimezone(timezone.utc).replace(tzinfo=None)


def day_label(day, today=None):
    today = today or nairobi_today()
    if day == today:
        return "Today"
    if day == today - timedelta(days=1):
        return "Yesterday"
    if day.year == today.year:
        return day.strftime("%a %d %b")
    return day.strftime("%a %d %b %Y")


def parse_day(value):
    """A YYYY-MM-DD query value, or None when missing or invalid."""
    try:
        return date.fromisoformat((value or "")[:10])
    except ValueError:
        return None


def read_period(default="all"):
    period = request.args.get("period", default)
    return period if period in PERIODS else default


def filter_by_time(query, column, period="all", day=None):
    """Limit a query to one Nairobi day, or to the chosen recent period."""
    if day is not None:
        return query.filter(column >= day_start_utc(day), column < day_start_utc(day + timedelta(days=1)))
    days_back = PERIODS[period][1]
    if days_back is None:
        return query
    return query.filter(column >= day_start_utc(nairobi_today() - timedelta(days=days_back)))


def read_page():
    page = request.args.get("page", 1, type=int) or 1
    return min(max(page, 1), MAX_SHOWN // PAGE_SIZE)


def is_partial():
    """True when the list asks for just the next batch of rows ("Show more")."""
    return request.args.get("partial") == "1"


@dataclass
class Batch:
    """The rows to draw now. A full page draws pages 1..n so a plain link
    to ?page=n works without JavaScript; the "Show more" fetch draws only page n."""

    rows: list
    page: int
    total: int
    shown: int = 0

    @property
    def has_more(self):
        return self.shown < self.total and self.shown < MAX_SHOWN

    @property
    def remaining(self):
        return max(self.total - self.shown, 0)


def take_batch(ordered_query, total, page=None, partial=None):
    page = page or read_page()
    partial = is_partial() if partial is None else partial
    if partial:
        rows = ordered_query.offset((page - 1) * PAGE_SIZE).limit(PAGE_SIZE).all()
    else:
        rows = ordered_query.limit(page * PAGE_SIZE).all()
    return Batch(rows=rows, page=page, total=total, shown=min(page * PAGE_SIZE, total))


@dataclass
class DayGroup:
    day: date
    label: str
    rows: list = field(default_factory=list)
    count: int = 0
    total: Decimal = Decimal("0")

    @property
    def key(self):
        return self.day.isoformat()


def group_by_day(rows, when, totals=None):
    """Group rows (already newest first) under their Nairobi day.

    `when(row)` gives the stored timestamp. `totals` maps day -> (count, total)
    for the whole day, so a day split across "Show more" batches still shows
    its full figures.
    """
    today = nairobi_today()
    groups = []
    for row in rows:
        stamp = when(row)
        day = nairobi_day(stamp) if stamp else today
        if not groups or groups[-1].day != day:
            count, total = (totals or {}).get(day, (0, Decimal("0")))
            groups.append(DayGroup(day=day, label=day_label(day, today), count=count, total=total))
        groups[-1].rows.append(row)
    return groups


def day_totals(query, date_column, amount_column, days):
    """{day: (count, total)} for the given Nairobi days, using the query's filters."""
    if not days:
        return {}
    first, last = min(days), max(days)
    rows = (
        query.with_entities(date_column, amount_column)
        .filter(date_column >= day_start_utc(first), date_column < day_start_utc(last + timedelta(days=1)))
        .all()
    )
    totals = {}
    for stamp, amount in rows:
        day = nairobi_day(stamp)
        count, total = totals.get(day, (0, Decimal("0")))
        totals[day] = (count + 1, total + Decimal(amount or 0))
    return totals


def pulse(query, date_column, amount_column, days=PULSE_DAYS):
    """The last `days` Nairobi days, oldest first, each with its count, total
    and bar height (percent of the busiest day)."""
    today = nairobi_today()
    window = [today - timedelta(days=offset) for offset in range(days - 1, -1, -1)]
    totals = day_totals(query, date_column, amount_column, window)
    peak = max((total for _, total in totals.values()), default=Decimal("0"))
    bars = []
    for day in window:
        count, total = totals.get(day, (0, Decimal("0")))
        bars.append({
            "day": day,
            "key": day.isoformat(),
            "weekday": "Today" if day == today else day.strftime("%a"),
            "date": day.strftime("%d %b"),
            "count": count,
            "total": total,
            "height": int(total * 100 / peak) if peak else 0,
        })
    return bars


def initials(name):
    parts = (name or "").split()
    if not parts:
        return "?"
    return (parts[0][0] + (parts[-1][0] if len(parts) > 1 else "")).upper()
