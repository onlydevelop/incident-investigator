import json

import pytest

from delta_ticker import TickerPayload

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
        "symbol", "product_id", "strike_price", "timestamp_us",
        "spot_price", "mark_price", "best_bid", "best_ask", "delta",
    }


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


def test_kafka_key_and_value():
    p = TickerPayload.from_message(MESSAGE)
    assert p.key() == b"C-BTC-79500-250926"
    assert json.loads(p.to_json())["mark_price"] == 4522.51410849
