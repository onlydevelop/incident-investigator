"""Tests run against the real Postgres from ../infra, each run in its own throwaway schema,
so they never touch the `positions` table the running service uses."""
import os
import uuid
from datetime import datetime, timedelta, timezone

import psycopg
import pytest
from psycopg_pool import ConnectionPool

from order_service.config import DATABASE_TIMEZONE, DATABASE_URL
from order_service.store import PositionStore

TEST_DATABASE_URL = os.environ.get("TEST_DATABASE_URL", DATABASE_URL)
IST = timezone(timedelta(hours=5, minutes=30), "IST")


@pytest.fixture(scope="session")
def schema():
    name = f"test_{uuid.uuid4().hex[:12]}"
    with psycopg.connect(TEST_DATABASE_URL, autocommit=True) as conn:
        conn.execute(f"CREATE SCHEMA {name}")
    yield name
    with psycopg.connect(TEST_DATABASE_URL, autocommit=True) as conn:
        conn.execute(f"DROP SCHEMA {name} CASCADE")


@pytest.fixture(scope="session")
def pool(schema):
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
    store = PositionStore(pool)
    store.create_schema()
    yield store
    with pool.connection() as conn:
        conn.execute("TRUNCATE positions RESTART IDENTITY")


@pytest.fixture
def later():
    """Tick times after now (exchange ticks are in IST, like market-data-service's payloads)."""
    now = datetime.now(IST)
    return lambda seconds: now + timedelta(seconds=seconds)
