from decimal import Decimal

import psycopg
import pytest
from fastapi.testclient import TestClient

from fakes import CALL, PUT, FakeSymbols
from order_service.api import create_app


class TestCreate:
    def test_stores_pending_position_and_subscribes(self, create, symbols, store):
        r = create(qty=2)

        assert r.status_code == 201
        body = r.json()
        assert body["id"] == 1
        assert (body["symbol"], body["side"], body["qty"], body["status"]) == (CALL, "buy", 2.0, "pending")
        assert body["entry_price"] is None and body["current_price"] is None
        assert body["time"].endswith("+05:30")
        assert symbols.subscribed == [CALL]
        assert store.get(1) is not None

    def test_accepts_decimal_qty(self, create):
        assert create(qty=0.5).json()["qty"] == 0.5

    def test_rolls_back_when_market_data_fails(self, store):
        client = TestClient(create_app(store, FakeSymbols(fail=True)))

        r = client.post("/positions", json={"symbol": CALL, "side": "buy", "qty": 1})

        assert r.status_code == 502
        assert "no position was created" in r.json()["detail"]
        assert store.list() == []

    @pytest.mark.parametrize("body", [
        pytest.param({"symbol": "BTCUSD", "side": "buy", "qty": 1}, id="not-an-option"),
        pytest.param({"symbol": CALL, "side": "long", "qty": 1}, id="bad-side"),
        pytest.param({"symbol": CALL, "side": "buy", "qty": 0}, id="zero-qty"),
        pytest.param({"symbol": CALL, "side": "buy", "qty": -1}, id="negative-qty"),
        pytest.param({"symbol": CALL, "side": "buy"}, id="missing-qty"),
        pytest.param({"symbol": CALL, "side": "buy", "quantity": 1}, id="misspelt-field"),
        pytest.param({"symbol": CALL, "side": "buy", "qty": 1, "entry_price": 100}, id="extra-field"),
    ])
    def test_rejects_invalid_body(self, client, symbols, body):
        assert client.post("/positions", json=body).status_code == 422
        assert symbols.subscribed == []


class TestRead:
    def test_get_returns_prices_as_numbers(self, create, client, store, later):
        create(side="sell")
        store.apply_tick(CALL, later(1), bid=Decimal("5121.5"), ask=Decimal("5177"))
        store.apply_tick(CALL, later(2), bid=Decimal("5100"), ask=Decimal("5150.25"))

        body = client.get("/positions/1").json()

        assert body["status"] == "open"
        assert body["entry_price"] == 5121.5
        assert body["current_price"] == 5150.25
        assert body["entry_time"].endswith("+05:30") and body["current_price_time"].endswith("+05:30")

    def test_get_unknown_is_404(self, client):
        r = client.get("/positions/99")

        assert r.status_code == 404
        assert r.json() == {"detail": "Position 99 not found"}

    def test_get_rejects_non_positive_id(self, client):
        assert client.get("/positions/0").status_code == 422

    def test_list_with_filters(self, create, client, store, later):
        create(symbol=CALL)
        create(symbol=PUT)
        store.apply_tick(PUT, later(1), bid=Decimal("1"), ask=Decimal("2"))

        def ids(**params):
            return [p["id"] for p in client.get("/positions", params=params).json()["positions"]]

        assert ids() == [1, 2]
        assert ids(symbol=PUT) == [2]
        assert ids(status="pending") == [1]
        assert ids(symbol=CALL, status="open") == []

    @pytest.mark.parametrize("params", [{"status": "closed"}, {"symbol": "BTCUSD"}, {"side": "buy"}])
    def test_list_rejects_invalid_filters(self, client, params):
        assert client.get("/positions", params=params).status_code == 422

    def test_list_count_matches_positions(self, create, client):
        assert client.get("/positions").json() == {"positions": [], "count": 0}
        create()
        body = client.get("/positions").json()
        assert body["count"] == len(body["positions"]) == 1


class TestDelete:
    def test_delete_then_404(self, create, client):
        create()

        assert client.delete("/positions/1").status_code == 204
        assert client.get("/positions/1").status_code == 404
        assert client.delete("/positions/1").status_code == 404


class TestHealth:
    def test_ok(self, client):
        assert client.get("/health").json() == {"status": "ok"}

    def test_postgres_down_is_503(self, symbols):
        class DownStore:
            def ping(self):
                raise psycopg.OperationalError("connection refused")

        r = TestClient(create_app(DownStore(), symbols)).get("/health")

        assert r.status_code == 503
        assert r.json() == {"detail": "Postgres unavailable: connection refused"}


def test_openapi_documents_request_and_error_models(client):
    spec = client.get("/openapi.json").json()
    schemas = spec["components"]["schemas"]
    post = spec["paths"]["/positions"]["post"]

    def ref(response):
        return response["content"]["application/json"]["schema"]["$ref"].rsplit("/", 1)[-1]

    assert ref(post["requestBody"]) == "PositionCreate"
    assert ref(post["responses"]["201"]) == "PositionResponse"
    assert ref(post["responses"]["502"]) == "ErrorResponse"
    assert ref(spec["paths"]["/positions/{position_id}"]["get"]["responses"]["404"]) == "ErrorResponse"
    assert schemas["PositionCreate"]["additionalProperties"] is False
    assert "count" in schemas["PositionListResponse"]["properties"]
