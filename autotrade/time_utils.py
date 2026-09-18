from __future__ import annotations

from datetime import UTC, datetime, timedelta, timezone

WIB = timezone(timedelta(hours=7), name="WIB")


def parse_timestamp(value: str | datetime) -> datetime:
    """Parse an aware timestamp and normalize it to UTC."""
    parsed = (
        value
        if isinstance(value, datetime)
        else datetime.fromisoformat(value.replace("Z", "+00:00"))
    )
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("timestamp must include a UTC offset")
    return parsed.astimezone(UTC)


def canonical_utc(value: str | datetime) -> str:
    return parse_timestamp(value).isoformat(timespec="microseconds").replace("+00:00", "Z")


def utc_to_wib(value: str | datetime) -> str:
    return parse_timestamp(value).astimezone(WIB).isoformat(timespec="microseconds")


def duration_ms(start: str | datetime, end: str | datetime) -> int:
    return max(0, round((parse_timestamp(end) - parse_timestamp(start)).total_seconds() * 1000))
