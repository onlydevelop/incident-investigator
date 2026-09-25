import json
from decimal import Decimal

import pytest

from order_service import PositionUpdater, Side, Status

CALL = "C-BTC-80000-091026"


def tick(time, best_bid=5121.0, best_ask=5177.0, **extra):
    """A market-data-service TickerPayload as it arrives on Kafka."""
    return json.dumps({
        "symbol": CALL, "product_id": 153512, "strike_price": 80000.0, "time": time.isoformat(),
        "spot_price": 84330.4, "mark_price": 5148.09436923,
        "best_bid": best_bid, "best_ask": best_ask, "delta": 0.77664966, **extra,
    }).encode()


def test_handle_opens_then_updates(store, later):
    p = store.create(CALL, Side.BUY, Decimal("1"))
    updater = PositionUpdater(store)

    assert updater.handle(tick(later(1))) == (1, 0)
    assert updater.handle(tick(later(2), best_bid=5200.1, best_ask=5250.0)) == (0, 1)

    p = store.get(p.id)
    assert p.status == Status.OPEN
    assert p.entry_price == Decimal("5177.0")
    # Floats are converted via str, so there's no binary-float noise in the numeric column.
    assert p.current_price == Decimal("5200.1")
    assert p.entry_time == later(1)


def test_handle_accepts_null_quotes(store, later):
    store.create(CALL, Side.BUY, Decimal("1"))

    assert PositionUpdater(store).handle(tick(later(1), best_ask=None)) == (0, 0)


def test_handle_logs_openings(store, later, capsys):
    store.create(CALL, Side.SELL, Decimal("1"))

    PositionUpdater(store).handle(tick(later(1)))

    assert f"Opened 1 position(s) in {CALL} at bid=5121.0 ask=5177.0" in capsys.readouterr().out


@pytest.mark.parametrize("value", [b"not json", b'{"time": "2026-09-25T14:00:00+05:30"}', b'{"symbol": "X", "time": "yesterday"}'])
def test_handle_rejects_bad_messages(store, value):
    with pytest.raises((ValueError, KeyError)):
        PositionUpdater(store).handle(value)
