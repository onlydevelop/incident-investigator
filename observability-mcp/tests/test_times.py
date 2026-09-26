from datetime import datetime, timedelta, timezone

import pytest

from observability_mcp.times import iso, iso_from_ns, parse_duration, parse_range, parse_time

NOW = datetime(2026, 9, 26, 1, 30, tzinfo=timezone.utc)


@pytest.mark.parametrize("value, expected", [
    (None, NOW),
    ("now", NOW),
    ("15m", NOW - timedelta(minutes=15)),
    ("-15m", NOW - timedelta(minutes=15)),
    ("now-2h", NOW - timedelta(hours=2)),
    ("1.5h", NOW - timedelta(minutes=90)),
    ("1790386200", datetime(2026, 9, 26, 1, 30, tzinfo=timezone.utc)),
    ("2026-09-26T01:00:00Z", datetime(2026, 9, 26, 1, 0, tzinfo=timezone.utc)),
    ("2026-09-26T07:00:00+05:30", datetime(2026, 9, 26, 1, 30, tzinfo=timezone.utc)),
])
def test_parse_time(value, expected):
    assert parse_time(value, NOW) == expected


@pytest.mark.parametrize("value, message", [
    ("yesterday", "not a time"),
    ("2026-09-26T01:00:00", "no time zone"),
])
def test_parse_time_rejects(value, message):
    with pytest.raises(ValueError, match=message):
        parse_time(value, NOW)


def test_parse_duration():
    assert parse_duration("250ms") == timedelta(milliseconds=250)
    assert parse_duration("1w") == timedelta(weeks=1)
    with pytest.raises(ValueError, match="not a duration"):
        parse_duration("5 minutes")


def test_range_must_be_ordered():
    assert parse_range("1h", "now", NOW) == (NOW - timedelta(hours=1), NOW)
    with pytest.raises(ValueError, match="must be before"):
        parse_range("now", "1h", NOW)


def test_iso_formats():
    assert iso(NOW) == "2026-09-26T01:30:00.000Z"
    assert iso_from_ns("1790386200123456789") == "2026-09-26T01:30:00.123Z"
