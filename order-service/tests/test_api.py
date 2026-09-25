from decimal import Decimal

import psycopg
import pytest
from fastapi.testclient import TestClient

from order_service.api import create_app
from order_service.market_data import MarketDataError

CALL = "C-BTC-80000-091026"
PUT = "P-BTC-80000-091026"


class FakeSymbols:
    def __init__(self, fail=False):
        self.fail = fail
        self.subscribed = []

    def subscribe(self, symbol):
        if self.fail:
            raise MarketDataError("market-data-service unreachable: ConnectError()")
        self.subscribed.append(symbol)
        return True


@pytest.fixture
def symbols():
    return FakeSymbols()


@pytest.fixture
def client(store, symbols):
    return TestClient(create_app(store, symbols))


def create(client, symbol=CALL, side="buy", qty=1):
    return client.post("/positions", json={"symbol": symbol, "side": side, "qty": qty})


def test_create_stores_pending_position_and_subscribes(client, symbols, store):
    r = create(client, qty=2)

    assert r.status_code == 201
    body = r.json()
    assert body["id"] == 1
    assert (body["symbol"], body["side"], body["qty"], body["status"]) == (CALL, "buy", 2.0, "pending")
    assert body["entry_price"] is None and body["current_price"] is None
    assert body["time"].endswith("+05:30")
    assert symbols.subscribed == [CALL]
    assert store.get(1) is not None


def test_create_rolls_back_when_market_data_fails(store):
    client = TestClient(create_app(store, FakeSymbols(fail=True)))

    r = create(client)

    assert r.status_code == 502
    assert "no position was created" in r.json()["detail"]
    assert store.list() == []


@pytest.mark.parametrize("body", [
    {"symbol": "BTCUSD", "side": "buy", "qty": 1},
    {"symbol": CALL, "side": "long", "qty": 1},
    {"symbol": CALL, "side": "buy", "qty": 0},
    {"symbol": CALL, "side": "buy", "qty": -1},
    {"symbol": CALL, "side": "buy"},
])
def test_create_rejects_invalid_body(client, symbols, body):
    assert client.post("/positions", json=body).status_code == 422
    assert symbols.subscribed == []


def test_get_returns_prices_as_numbers(client, store, later):
    create(client, side="sell")
    store.apply_tick(CALL, later(1), bid=Decimal("5121.5"), ask=Decimal("5177"))
    store.apply_tick(CALL, later(2), bid=Decimal("5100"), ask=Decimal("5150.25"))

    body = client.get("/positions/1").json()

    assert body["status"] == "open"
    assert body["entry_price"] == 5121.5
    assert body["current_price"] == 5150.25
    assert body["entry_time"].endswith("+05:30") and body["current_price_time"].endswith("+05:30")


def test_get_unknown_is_404(client):
    r = client.get("/positions/99")

    assert r.status_code == 404
    assert r.json() == {"detail": "Position 99 not found"}


def test_list_with_filters(client, store, later):
    create(client, symbol=CALL)
    create(client, symbol=PUT)
    store.apply_tick(PUT, later(1), bid=Decimal("1"), ask=Decimal("2"))

    assert client.get("/positions").json()["count"] == 2
    assert [p["id"] for p in client.get("/positions", params={"symbol": PUT}).json()["positions"]] == [2]
    assert [p["id"] for p in client.get("/positions", params={"status": "pending"}).json()["positions"]] == [1]
    assert client.get("/positions", params={"status": "closed"}).status_code == 422


def test_delete(client):
    create(client)

    assert client.delete("/positions/1").status_code == 204
    assert client.get("/positions/1").status_code == 404
    assert client.delete("/positions/1").status_code == 404


def test_health(client):
    assert client.get("/health").json() == {"status": "ok"}


def test_postgres_down_is_503(symbols):
    class DownStore:
        def ping(self):
            raise psycopg.OperationalError("connection refused")

    r = TestClient(create_app(DownStore(), symbols)).get("/health")

    assert r.status_code == 503
    assert r.json()["detail"].startswith("Postgres unavailable")
