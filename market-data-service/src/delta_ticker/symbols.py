import logging
import threading
from typing import Callable, Optional

import redis

from delta_ticker.config import REDIS_URL, SYMBOL_REFRESH_SECONDS, SYMBOLS_KEY

log = logging.getLogger(__name__)


class SymbolRegistry:
    """The symbols to subscribe to, kept in a Redis set with no expiry.

    Manage it through the symbols API, `make symbols-*`, or SADD / SREM / DEL on `KEY`.
    Apart from `get`, methods raise redis.RedisError so callers can report failures.
    """

    KEY = SYMBOLS_KEY

    def __init__(self, client: redis.Redis):
        self.client = client

    @classmethod
    def from_url(cls, url: str = REDIS_URL) -> "SymbolRegistry":
        return cls(redis.Redis.from_url(url))

    def get(self) -> Optional[list[str]]:
        """Sorted symbols, or None if Redis couldn't be read (so callers keep what they have)."""
        try:
            return self.members()
        except redis.RedisError as e:
            log.error(f"Failed to read symbols from {self.KEY}: {e!r}", extra={"event": "symbols_read_failed"})
            return None

    def members(self) -> list[str]:
        members = self.client.smembers(self.KEY)
        return sorted(m.decode("utf-8") if isinstance(m, bytes) else m for m in members)

    def contains(self, symbol: str) -> bool:
        return bool(self.client.sismember(self.KEY, symbol))

    def add(self, symbols: list[str]) -> int:
        """Adds symbols; returns how many were new."""
        return self.client.sadd(self.KEY, *symbols) if symbols else 0

    def remove(self, symbols: list[str]) -> int:
        """Removes symbols; returns how many were present."""
        return self.client.srem(self.KEY, *symbols) if symbols else 0

    def replace(self, symbols: list[str]):
        """Swaps the whole set in one transaction, so a refresh never sees it half-written."""
        pipe = self.client.pipeline(transaction=True)
        pipe.delete(self.KEY)
        if symbols:
            pipe.sadd(self.KEY, *symbols)
        pipe.execute()

    def clear(self):
        self.client.delete(self.KEY)

    def ping(self) -> bool:
        return bool(self.client.ping())


class SymbolRefresher(threading.Thread):
    """Background thread that re-reads the registry every `interval_seconds`
    and passes the result to `on_symbols`."""

    def __init__(
        self,
        registry: SymbolRegistry,
        on_symbols: Callable[[list[str]], None],
        interval_seconds: float = SYMBOL_REFRESH_SECONDS,
    ):
        super().__init__(name="symbol-refresher", daemon=True)
        self.registry = registry
        self.on_symbols = on_symbols
        self.interval_seconds = interval_seconds
        self._stop_event = threading.Event()

    def refresh(self):
        symbols = self.registry.get()
        if symbols is not None:
            self.on_symbols(symbols)

    def run(self):
        while not self._stop_event.wait(self.interval_seconds):
            self.refresh()

    def stop(self):
        self._stop_event.set()
