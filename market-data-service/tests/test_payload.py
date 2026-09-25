import json
from datetime import datetime, timezone

import pytest

from delta_ticker import TickerPayload
from delta_ticker.payload import epoch_us_to_ist

MESSAGE = {
    "type": "v2/ticker",
    "symbol": "C-BTC-79500-250926",
    "product_id": 151279,
    "contract_type": "call_options",
    "underlying_asset_symbol": "BTC",
    "strike_price": "79500",
    "timestamp": 1790318040249114,
    "spot_price": "84020",
    "mark_price": "4522.51410849",
    "quotes": {
        "best_bid": "4496", "bid_size": "4104",
        "best_ask": "4544", "ask_size": "2731",
        "bid_iv": "0.000005", "ask_iv": "1.17031194", "mark_iv": "0.85322924",
        "impact_mid_price": None,
    },
    "greeks": {
        "delta": "0.99539805", "gamma": "0.00000750", "rho": "0.49072952",
        "theta": "-53.09022913", "vega": "0.28100099",
    },
    "oi": "0.7850",
    "volume": 0.601,
}


def test_from_message_parses_strings_to_floats():
    p = TickerPayload.from_message(MESSAGE)
    assert p.symbol == "C-BTC-79500-250926"
    assert p.strike_price == 79500.0
    assert p.best_bid == 4496.0
    assert p.best_ask == 4544.0
    assert p.delta == 0.99539805


def test_payload_has_only_the_kept_fields():
    assert set(TickerPayload.from_message(MESSAGE).to_dict()) == {
        "symbol", "product_id", "strike_price", "time",
        "spot_price", "mark_price", "best_bid", "best_ask", "delta",
    }


def test_timestamp_is_converted_to_ist():
    # 1790318040249114 us = 2026-09-25T06:34:00.249114Z
    p = TickerPayload.from_message(MESSAGE)
    assert p.time == "2026-09-25T12:04:00.249114+05:30"


def test_timestamp_keeps_microseconds_without_float_rounding():
    assert epoch_us_to_ist(1790318040000007) == "2026-09-25T12:04:00.000007+05:30"
    assert datetime.fromisoformat(epoch_us_to_ist(1790318040249114)) == datetime(
        2026, 9, 25, 6, 34, 0, 249114, tzinfo=timezone.utc
    )


def test_missing_greeks_and_null_quotes_become_none():
    msg = {**MESSAGE, "greeks": None, "quotes": {"best_bid": None, "best_ask": ""}}
    p = TickerPayload.from_message(msg)
    assert p.delta is None
    assert p.best_bid is None
    assert p.best_ask is None


def test_missing_required_field_raises():
    msg = {k: v for k, v in MESSAGE.items() if k != "symbol"}
    with pytest.raises(KeyError):
        TickerPayload.from_message(msg)


def test_json_round_trip():
    p = TickerPayload.from_message(MESSAGE)
    assert TickerPayload.from_json(p.to_json()) == p


def test_from_json_ignores_fields_from_older_versions():
    old = {**TickerPayload.from_message(MESSAGE).to_dict(), "source": "delta.exchange", "vega": 0.28}
    assert TickerPayload.from_json(json.dumps(old)) == TickerPayload.from_message(MESSAGE)


@pytest.mark.parametrize("old_key", ["timestamp_ist", "timestamp_us"])
def test_from_json_reads_time_from_older_versions(old_key):
    current = TickerPayload.from_message(MESSAGE)
    old = current.to_dict()
    old_time = old.pop("time")
    old[old_key] = old_time if old_key == "timestamp_ist" else MESSAGE["timestamp"]
    assert TickerPayload.from_json(json.dumps(old)) == current


def test_kafka_key_and_value():
    p = TickerPayload.from_message(MESSAGE)
    assert p.key() == b"C-BTC-79500-250926"
    assert json.loads(p.to_json())["mark_price"] == 4522.51410849
