import threading
from typing import Callable, Optional

import redis

from delta_ticker.config import REDIS_URL, SYMBOL_REFRESH_SECONDS, SYMBOLS_KEY


class SymbolRegistry:
    """The symbols to subscribe to, kept in a Redis set with no expiry.

    Manage it with SADD / SREM / DEL on `KEY`, e.g. via `make symbols-add`.
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
            members = self.client.smembers(self.KEY)
        except redis.RedisError as e:
            print(f"Failed to read symbols from {self.KEY}: {e!r}")
            return None
        return sorted(m.decode("utf-8") if isinstance(m, bytes) else m for m in members)


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
