from typing import Optional

import redis

from delta_ticker.payload import TickerPayload

DEFAULT_REDIS_URL = "redis://localhost:6379/0"


class TickerCache:
    """Keeps the latest TickerPayload per symbol in Redis, expiring after `ttl_seconds`."""

    KEY_PREFIX = "ticker:latest:"

    def __init__(self, client: redis.Redis, ttl_seconds: int = 10):
        self.client = client
        self.ttl_seconds = ttl_seconds

    @classmethod
    def from_url(cls, url: str = DEFAULT_REDIS_URL, ttl_seconds: int = 10) -> "TickerCache":
        return cls(redis.Redis.from_url(url), ttl_seconds)

    @classmethod
    def key_for(cls, symbol: str) -> str:
        return f"{cls.KEY_PREFIX}{symbol}"

    def store(self, payload: TickerPayload):
        """Overwrites the symbol's entry and resets its TTL. Redis errors are logged, not raised,
        so a Redis outage doesn't take down the websocket feed."""
        try:
            self.client.set(self.key_for(payload.symbol), payload.to_json(), ex=self.ttl_seconds)
        except redis.RedisError as e:
            print(f"Failed to cache {payload.symbol}: {e!r}")

    def get(self, symbol: str) -> Optional[bytes]:
        """Latest payload JSON for `symbol`, or None if nothing arrived within the TTL."""
        return self.client.get(self.key_for(symbol))
