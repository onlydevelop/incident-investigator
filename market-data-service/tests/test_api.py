import fakeredis
import pytest
import redis
from fastapi.testclient import TestClient

from delta_ticker.api import create_app
from delta_ticker.cache import TickerCache
from delta_ticker.payload import TickerPayload
from delta_ticker.symbols import SymbolRegistry

from test_payload import MESSAGE

CALL = "C-BTC-80000-091026"
PUT = "P-BTC-80000-091026"


@pytest.fixture
def redis_client():
    return fakeredis.FakeRedis()


@pytest.fixture
def registry(redis_client):
    return SymbolRegistry(redis_client)


@pytest.fixture
def cache(redis_client):
    return TickerCache(redis_client)


@pytest.fixture
def client(registry, cache):
    return TestClient(create_app(registry, cache))


def payload_for(symbol: str) -> TickerPayload:
    return TickerPayload.from_message({**MESSAGE, "symbol": symbol})


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


def test_tickers_empty(client):
    assert client.get("/tickers").json() == {"tickers": [], "count": 0}


def test_tickers_lists_all_cached_sorted(client, cache, redis_client):
    cache.store(payload_for(PUT))
    cache.store(payload_for(CALL))
    redis_client.set("unrelated:key", "x")

    r = client.get("/tickers")

    assert r.status_code == 200
    body = r.json()
    assert body["count"] == 2
    assert [t["symbol"] for t in body["tickers"]] == [CALL, PUT]
    assert body["tickers"][0] == payload_for(CALL).to_dict()


def test_get_ticker(client, cache):
    cache.store(payload_for(CALL))
    r = client.get(f"/tickers/{CALL}")
    assert r.status_code == 200
    assert r.json() == payload_for(CALL).to_dict()


def test_get_ticker_missing_is_404(client):
    r = client.get(f"/tickers/{CALL}")
    assert r.status_code == 404
    assert CALL in r.json()["detail"]


def test_expired_ticker_is_gone(client, cache, redis_client):
    cache.store(payload_for(CALL))
    redis_client.delete(TickerCache.key_for(CALL))  # what TTL expiry does
    assert client.get(f"/tickers/{CALL}").status_code == 404
    assert client.get("/tickers").json()["count"] == 0


def test_redis_down_returns_503():
    class DownRedis:
        def __getattr__(self, name):
            def fail(*args, **kwargs):
                raise redis.ConnectionError("down")
            return fail

    down = DownRedis()
    client = TestClient(create_app(SymbolRegistry(down), TickerCache(down)))
    r = client.get("/symbols")
    assert r.status_code == 503
    assert "Redis unavailable" in r.json()["detail"]
    assert client.get("/health").status_code == 503
    assert client.get("/tickers").status_code == 503
    assert client.get(f"/tickers/{CALL}").status_code == 503
