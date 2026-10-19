from datetime import datetime, timezone
from zoneinfo import ZoneInfo


NAIROBI_TIMEZONE = ZoneInfo("Africa/Nairobi")


def format_nairobi_datetime(value, format_string="%d %b %Y %H:%M"):
    if value is None:
        return ""
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(NAIROBI_TIMEZONE).strftime(format_string)


def days_since_nairobi_date(value, now=None):
    """Count calendar days since a stored UTC timestamp in Nairobi time."""
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    now = now or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)
    sale_date = value.astimezone(NAIROBI_TIMEZONE).date()
    today = now.astimezone(NAIROBI_TIMEZONE).date()
    return max((today - sale_date).days, 0)
