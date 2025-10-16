# utils/time.py
# Time and duration formatting and parsing utilities.

from datetime import datetime, timezone, timedelta
import re

def format_timestamp(ts: int, format_type: str = "relative") -> str:
    """
    Formats a UNIX timestamp into a human-readable string.
    format_type can be 'relative' (e.g., "2 hours ago") or 'full' (e.g., "2023-10-27 15:00 UTC").
    """
    if not ts:
        return "N/A"

    dt_object = datetime.fromtimestamp(ts, tz=timezone.utc)

    if format_type == "full":
        return dt_object.strftime("%Y-%m-%d %H:%M UTC")

    # Relative time calculation
    delta = datetime.now(timezone.utc) - dt_object

    if delta.days > 1:
        return f"{delta.days} days ago"
    if delta.days == 1:
        return "1 day ago"
    if delta.total_seconds() >= 3600:
        hours = int(delta.total_seconds() // 3600)
        return f"{hours} hour{'s' if hours > 1 else ''} ago"
    if delta.total_seconds() >= 60:
        minutes = int(delta.total_seconds() // 60)
        return f"{minutes} minute{'s' if minutes > 1 else ''} ago"
    return "Just now"

def parse_duration(duration_str: str) -> timedelta | None:
    """
    Parses a duration string (e.g., '7d', '12h', '30m') into a timedelta object.
    Returns None if the format is invalid.
    """
    match = re.match(r"(\d+)\s*([mhd])$", duration_str.lower().strip())
    if not match:
        return None

    value, unit = int(match.group(1)), match.group(2)

    if unit == 'm':
        return timedelta(minutes=value)
    if unit == 'h':
        return timedelta(hours=value)
    if unit == 'd':
        return timedelta(days=value)

    return None