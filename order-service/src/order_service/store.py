from datetime import datetime
from decimal import Decimal
from importlib.resources import files
from typing import Callable, Optional

from psycopg.rows import class_row
from psycopg_pool import ConnectionPool

from order_service.config import DATABASE_TIMEOUT_SECONDS, DATABASE_TIMEZONE, DATABASE_URL
from order_service.models import Position, Side, Status

COLUMNS = "id, time, symbol, side, qty, status, entry_price, entry_time, current_price, current_price_time"

# The price a position trades at: a buy lifts the ask, a sell hits the bid.
ENTRY_PRICE = "CASE side WHEN 'buy' THEN %(ask)s::numeric ELSE %(bid)s::numeric END"
# The price it would close at now: a long sells at the bid, a short buys back at the ask.
CLOSE_PRICE = "CASE side WHEN 'buy' THEN %(bid)s::numeric ELSE %(ask)s::numeric END"

# Ticks are applied in exchange time, so an old tick (a consumer replay, or one that was
# already in flight when the position was created) never sets or rolls back a price.
UPDATE_CURRENT = f"""
    UPDATE positions
       SET current_price = {CLOSE_PRICE}, current_price_time = %(time)s
     WHERE symbol = %(symbol)s AND status = 'open'
       AND entry_time < %(time)s
       AND (current_price_time IS NULL OR current_price_time < %(time)s)
       AND {CLOSE_PRICE} IS NOT NULL
"""

OPEN_PENDING = f"""
    UPDATE positions
       SET entry_price = {ENTRY_PRICE}, entry_time = %(time)s, status = 'open'
     WHERE symbol = %(symbol)s AND status = 'pending'
       AND time <= %(time)s
       AND {ENTRY_PRICE} IS NOT NULL
"""


class PositionStore:
    """Paper positions in the Postgres `positions` table (see schema.sql).

    Methods raise psycopg errors; the API turns connection failures into a 503.
    """

    def __init__(self, pool: ConnectionPool):
        self.pool = pool

    @classmethod
    def from_url(cls, url: str = DATABASE_URL) -> "PositionStore":
        pool = ConnectionPool(
            url,
            min_size=1,
            max_size=5,
            timeout=DATABASE_TIMEOUT_SECONDS,
            kwargs={"options": f"-c TimeZone={DATABASE_TIMEZONE}"},
            open=True,
        )
        store = cls(pool)
        store.create_schema()
        return store

    def create_schema(self):
        with self.pool.connection() as conn:
            conn.execute(files("order_service").joinpath("schema.sql").read_text())

    def create(
        self,
        symbol: str,
        side: Side,
        qty: Decimal,
        before_commit: Optional[Callable[[Position], None]] = None,
    ) -> Position:
        """Inserts a pending position. `before_commit` runs inside the transaction; if it raises,
        the insert is rolled back and the error propagates, so nothing is left half-created."""
        with self.pool.connection() as conn, conn.cursor(row_factory=class_row(Position)) as cur:
            cur.execute(
                f"INSERT INTO positions (symbol, side, qty) VALUES (%s, %s, %s) RETURNING {COLUMNS}",
                (symbol, side.value, qty),
            )
            position = cur.fetchone()
            if before_commit:
                before_commit(position)
            return position

    def get(self, position_id: int) -> Optional[Position]:
        with self.pool.connection() as conn, conn.cursor(row_factory=class_row(Position)) as cur:
            cur.execute(f"SELECT {COLUMNS} FROM positions WHERE id = %s", (position_id,))
            return cur.fetchone()

    def list(self, symbol: Optional[str] = None, status: Optional[Status] = None) -> list[Position]:
        """Oldest first, optionally filtered by symbol and/or status."""
        with self.pool.connection() as conn, conn.cursor(row_factory=class_row(Position)) as cur:
            cur.execute(
                f"""SELECT {COLUMNS} FROM positions
                     WHERE (%(symbol)s::text IS NULL OR symbol = %(symbol)s)
                       AND (%(status)s::text IS NULL OR status = %(status)s)
                     ORDER BY id""",
                {"symbol": symbol, "status": status.value if status else None},
            )
            return cur.fetchall()

    def delete(self, position_id: int) -> bool:
        """Returns False if there was no such position."""
        with self.pool.connection() as conn:
            return conn.execute("DELETE FROM positions WHERE id = %s", (position_id,)).rowcount > 0

    def apply_tick(
        self,
        symbol: str,
        time: datetime,
        bid: Optional[Decimal],
        ask: Optional[Decimal],
    ) -> tuple[int, int]:
        """Prices every position in `symbol` from one tick; returns (opened, updated).

        Open positions get a new current_price first, then pending ones created at or before the
        tick are opened at their entry price. The tick that opens a position therefore doesn't
        also set its current_price; the next one does. A side whose price is missing is skipped.
        """
        params = {"symbol": symbol, "time": time, "bid": bid, "ask": ask}
        with self.pool.connection() as conn:
            updated = conn.execute(UPDATE_CURRENT, params).rowcount
            opened = conn.execute(OPEN_PENDING, params).rowcount
            return opened, updated

    def ping(self) -> bool:
        with self.pool.connection() as conn:
            conn.execute("SELECT 1")
            return True

    def close(self):
        self.pool.close()
