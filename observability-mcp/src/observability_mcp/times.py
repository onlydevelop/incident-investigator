"""Times and durations as the tools accept them.

A time is `now`, a duration ago (`15m`, `-15m`, `now-15m`), an RFC 3339 timestamp
(`2026-09-26T01:30:00Z`, `2026-09-26T07:00:00+05:30`) or Unix seconds (`1790385600`).
A duration is a number and a unit: `ms`, `s`, `m`, `h`, `d` or `w` (`90s`, `1.5h`).
"""
import re
from datetime import datetime, timedelta, timezone

_UNITS = {"ms": 0.001, "s": 1, "m": 60, "h": 3600, "d": 86400, "w": 604800}
_DURATION = re.compile(r"^(\d+(?:\.\d+)?)(ms|s|m|h|d|w)$")


def parse_duration(value: str) -> timedelta:
    match = _DURATION.match(value.strip())
    if not match:
        raise ValueError(f"not a duration: {value!r}; use e.g. 30s, 5m, 1h, 2d")
    return timedelta(seconds=float(match[1]) * _UNITS[match[2]])


def parse_time(value: str | None, now: datetime | None = None) -> datetime:
    """A timezone-aware UTC datetime; None means now."""
    now = now or datetime.now(timezone.utc)
    text = (value or "now").strip()
    if text == "now":
        return now
    relative = text.removeprefix("now").removeprefix("-")
    if _DURATION.match(relative):
        return now - parse_duration(relative)
    try:
        return datetime.fromtimestamp(float(text), timezone.utc)
    except ValueError:
        pass
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        raise ValueError(
            f"not a time: {value!r}; use now, a duration ago (15m, now-2h), RFC 3339 or Unix seconds"
        ) from None
    if parsed.tzinfo is None:
        raise ValueError(f"time {value!r} has no time zone; add Z or an offset such as +05:30")
    return parsed.astimezone(timezone.utc)


def parse_range(start: str | None, end: str | None, now: datetime | None = None) -> tuple[datetime, datetime]:
    now = now or datetime.now(timezone.utc)
    start_at, end_at = parse_time(start, now), parse_time(end, now)
    if start_at >= end_at:
        raise ValueError(f"start ({start_at.isoformat()}) must be before end ({end_at.isoformat()})")
    return start_at, end_at


def iso(moment: datetime) -> str:
    return moment.astimezone(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def iso_from_ns(nanoseconds: int | str) -> str:
    ns = int(nanoseconds)
    return iso(datetime.fromtimestamp(ns // 1_000_000_000, timezone.utc) + timedelta(microseconds=ns % 1_000_000_000 // 1000))
