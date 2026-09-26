"""Construction and entry points: from_url, lazy dependencies, main()."""
import psycopg
import pytest
from fastapi.testclient import TestClient

from order_service import PositionStore, SymbolsClient
from order_service import api as api_module
from order_service import store as store_module

from conftest import TEST_DATABASE_URL
from fakes import CALL, FakeSymbols


@pytest.mark.db
def test_from_url_opens_pool_in_ist_and_applies_schema(monkeypatch, schema):
    real_pool = store_module.ConnectionPool
    seen = {}

    def pool_in_test_schema(url, **kwargs):
        # Keep from_url's settings, but point it at the throwaway schema rather than `public`.
        seen.update(kwargs)
        options = kwargs["kwargs"]["options"] + f" -c search_path={schema}"
        return real_pool(url, **{**kwargs, "kwargs": {"options": options}})

    monkeypatch.setattr(store_module, "ConnectionPool", pool_in_test_schema)

    store = PositionStore.from_url(TEST_DATABASE_URL)
    try:
        assert seen["timeout"] == 5
        assert "TimeZone=Asia/Kolkata" in seen["kwargs"]["options"]
        with store.pool.connection() as conn:
            assert conn.execute("SHOW TimeZone").fetchone()[0] == "Asia/Kolkata"
        assert store.list() == []  # the table exists
    finally:
        store.close()
    assert store.pool.closed


def test_from_url_closes_its_pool_if_postgres_is_unreachable(monkeypatch):
    pools = []

    class FakePool:
        def __init__(self, url, **kwargs):
            self.closed = False
            pools.append(self)

        def close(self):
            self.closed = True

    def unreachable(self):
        raise psycopg.OperationalError("couldn't get a connection after 5.00 sec")

    monkeypatch.setattr(store_module, "ConnectionPool", FakePool)
    monkeypatch.setattr(PositionStore, "create_schema", unreachable)

    with pytest.raises(psycopg.OperationalError):
        PositionStore.from_url("postgresql://nowhere/db")

    assert [p.closed for p in pools] == [True]
    assert pools[0] not in store_module._pools


def test_symbols_client_defaults_to_configured_url():
    client = SymbolsClient()

    assert str(client.client.base_url) == "http://localhost:8000"
    assert client.client.timeout.connect == 5


def test_app_creates_its_dependencies_lazily_and_once(monkeypatch, store):
    built = {"store": 0, "symbols": 0}
    symbols = FakeSymbols()

    def fake_store():
        built["store"] += 1
        return store

    def fake_symbols():
        built["symbols"] += 1
        return symbols

    monkeypatch.setattr(api_module.PositionStore, "from_url", staticmethod(fake_store))
    monkeypatch.setattr(api_module, "SymbolsClient", fake_symbols)

    client = TestClient(api_module.create_app())
    assert built == {"store": 0, "symbols": 0}

    client.get("/health")
    client.get("/health")
    assert built == {"store": 1, "symbols": 0}

    client.post("/positions", json={"symbol": CALL, "side": "buy", "qty": 1})
    client.post("/positions", json={"symbol": CALL, "side": "sell", "qty": 1})
    assert built == {"store": 1, "symbols": 1}
    assert symbols.subscribed == [CALL, CALL]


def test_api_main_sets_up_telemetry_and_runs_uvicorn(monkeypatch):
    calls = []
    monkeypatch.setattr(api_module.telemetry, "setup", lambda service: calls.append(("setup", service)))
    monkeypatch.setattr(api_module, "instrument", lambda app: calls.append(("instrument", app)))
    monkeypatch.setattr(api_module.uvicorn, "run", lambda app, **kwargs: calls.append(("run", app, kwargs)))

    api_module.main()

    assert calls == [
        ("setup", "orders-api"),
        ("instrument", api_module.app),
        ("run", api_module.app, {"host": "0.0.0.0", "port": 8001, "log_config": None}),
    ]
