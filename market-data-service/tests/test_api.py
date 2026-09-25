import fakeredis
import pytest
import redis
from fastapi.testclient import TestClient

from delta_ticker.api import create_app
from delta_ticker.symbols import SymbolRegistry

CALL = "C-BTC-80000-091026"
PUT = "P-BTC-80000-091026"


@pytest.fixture
def registry():
    return SymbolRegistry(fakeredis.FakeRedis())


@pytest.fixture
def client(registry):
    return TestClient(create_app(registry))


def test_list_starts_empty(client):
    r = client.get("/symbols")
    assert r.status_code == 200
    assert r.json()["symbols"] == []
    assert r.json()["count"] == 0


def test_post_adds_and_dedupes(client, registry):
    r = client.post("/symbols", json={"symbols": [PUT, CALL, CALL]})
    assert r.status_code == 201
    assert r.json()["symbols"] == [CALL, PUT]
    assert registry.members() == [CALL, PUT]


def test_post_keeps_existing_symbols(client, registry):
    registry.add([CALL])
    r = client.post("/symbols", json={"symbols": [PUT]})
    assert r.json()["symbols"] == [CALL, PUT]


def test_put_replaces_whole_list(client, registry):
    registry.add([CALL])
    r = client.put("/symbols", json={"symbols": [PUT]})
    assert r.status_code == 200
    assert registry.members() == [PUT]


def test_get_one(client, registry):
    registry.add([CALL])
    assert client.get(f"/symbols/{CALL}").json() == {"symbol": CALL}
    assert client.get(f"/symbols/{PUT}").status_code == 404


def test_put_one_is_idempotent(client, registry):
    assert client.put(f"/symbols/{CALL}").status_code == 201
    assert client.put(f"/symbols/{CALL}").status_code == 200
    assert registry.members() == [CALL]


def test_delete_one(client, registry):
    registry.add([CALL, PUT])
    assert client.delete(f"/symbols/{CALL}").status_code == 204
    assert registry.members() == [PUT]
    assert client.delete(f"/symbols/{CALL}").status_code == 404


def test_delete_all(client, registry):
    registry.add([CALL, PUT])
    assert client.delete("/symbols").status_code == 204
    assert registry.members() == []


@pytest.mark.parametrize("bad", ["BTCUSD", "c-btc-80000-091026", "C-BTC-80000-0910", "X-BTC-80000-091026"])
def test_rejects_non_option_symbols(client, registry, bad):
    assert client.post("/symbols", json={"symbols": [bad]}).status_code == 422
    assert client.put(f"/symbols/{bad}").status_code == 422
    assert registry.members() == []


def test_rejects_empty_body(client):
    assert client.post("/symbols", json={"symbols": []}).status_code == 422
    assert client.put("/symbols", json={"symbols": []}).status_code == 422


def test_redis_down_returns_503():
    class DownRedis:
        def __getattr__(self, name):
            def fail(*args, **kwargs):
                raise redis.ConnectionError("down")
            return fail

    client = TestClient(create_app(SymbolRegistry(DownRedis())))
    r = client.get("/symbols")
    assert r.status_code == 503
    assert "Redis unavailable" in r.json()["detail"]
    assert client.get("/health").status_code == 503
