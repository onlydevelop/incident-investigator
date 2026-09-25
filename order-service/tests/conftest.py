"""Shared fixtures.

Database tests run against the real Postgres from ../infra, each run in its own throwaway schema,
so they never touch the `positions` table the running service uses. Any test that uses a
database fixture (directly or through another fixture) is marked `db` automatically, so
`pytest -m "not db"` runs only the tests that need no Postgres.
"""
import os
import uuid
from datetime import datetime, timedelta, timezone
from decimal import Decimal
from typing import Callable

import httpx
import psycopg
import pytest
from fastapi.testclient import TestClient
from psycopg_pool import ConnectionPool

from order_service import Position, PositionStore, PositionUpdater, Side
from order_service.api import create_app
from order_service.config import DATABASE_TIMEZONE, DATABASE_URL

from fakes import CALL, FakeSymbols

TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL", DATABASE_URL)
IST = timezone(timedelta(hours=5, minutes=30), "IST")


DB_FIXTURES = {"schema", "pool", "store"}


def pytest_collection_modifyitems(items):
    for item in items:
        if DB_FIXTURES & set(getattr(item, "fixturenames", ())):
            item.add_marker(pytest.mark.db)


# --- Postgres ---

@pytest.fixture(scope="session")
def schema():
    """A schema that exists for one test session and is dropped afterwards."""
    name = f"test_{uuid.uuid4().hex[:12]}"
    with psycopg.connect(TEST_DATABASE_URL, autocommit=True) as conn:
        conn.execute(f"CREATE SCHEMA {name}")
    yield name
    with psycopg.connect(TEST_DATABASE_URL, autocommit=True) as conn:
        conn.execute(f"DROP SCHEMA {name} CASCADE")


@pytest.fixture(scope="session")
def pool(schema):
    """Connection pool pinned to the test schema, with the same session time zone as production."""
    pool = ConnectionPool(
        TEST_DATABASE_URL,
        min_size=1,
        max_size=2,
        kwargs={"options": f"-c search_path={schema} -c TimeZone={DATABASE_TIMEZONE}"},
        open=True,
    )
    yield pool
    pool.close()


@pytest.fixture
def store(pool):
    """A PositionStore on an empty `positions` table; ids restart at 1 for every test."""
    store = PositionStore(pool)
    store.create_schema()
    yield store
    with pool.connection() as conn:
        conn.execute("TRUNCATE positions RESTART IDENTITY")


@pytest.fixture
def make_position(store) -> Callable[..., Position]:
    """Factory: make_position(side=Side.SELL, qty="2", symbol=PUT) inserts a pending position."""
    def make(side: Side = Side.BUY, qty="1", symbol: str = CALL) -> Position:
        return store.create(symbol, side, Decimal(qty))
    return make


# --- Time ---

@pytest.fixture
def later() -> Callable[[float], datetime]:
    """later(seconds): an exchange tick time relative to now, in IST like market-data-service's payloads.
    Positive values are after any position created in the test; negative ones are before it."""
    now = datetime.now(IST)
    return lambda seconds: now + timedelta(seconds=seconds)


# --- API ---

@pytest.fixture
def symbols() -> FakeSymbols:
    return FakeSymbols()


@pytest.fixture
def client(store, symbols) -> TestClient:
    """TestClient for an app wired to the test store and a fake market-data-service."""
    return TestClient(create_app(store, symbols))


@pytest.fixture
def create(client) -> Callable[..., httpx.Response]:
    """create(side="sell", qty=2, symbol=PUT): POST /positions and return the response."""
    def post(symbol: str = CALL, side: str = "buy", qty=1):
        return client.post("/positions", json={"symbol": symbol, "side": side, "qty": qty})
    return post


# --- Updater ---

@pytest.fixture
def updater(store) -> PositionUpdater:
    return PositionUpdater(store)
