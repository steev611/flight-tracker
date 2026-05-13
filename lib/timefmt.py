"""Time-formatting helpers — show local + UTC side by side."""

import datetime
from zoneinfo import ZoneInfo


def fmt_dual(dt_utc: datetime.datetime, tz_name: str) -> str:
    """Render a UTC datetime as 'HH:MM TZ (HH:MM UTC)'."""
    local = dt_utc.astimezone(ZoneInfo(tz_name))
    return (f"{local.strftime('%Y-%m-%d %H:%M %Z')} "
            f"({dt_utc.strftime('%H:%M UTC')})")


def fmt_dual_from_ts(ts: int, tz_name: str) -> str:
    """Same as fmt_dual but takes a unix epoch."""
    dt = datetime.datetime.fromtimestamp(ts, datetime.timezone.utc)
    return fmt_dual(dt, tz_name)
