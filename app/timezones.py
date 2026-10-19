from datetime import timezone
from zoneinfo import ZoneInfo


NAIROBI_TIMEZONE = ZoneInfo("Africa/Nairobi")


def format_nairobi_datetime(value, format_string="%d %b %Y %H:%M"):
    if value is None:
        return ""
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(NAIROBI_TIMEZONE).strftime(format_string)
