"""Test doubles for the service's external collaborators: market-data-service and Kafka."""
import json
from datetime import datetime
from typing import Callable, Optional

from confluent_kafka import KafkaError

from order_service.market_data import MarketDataError

CALL = "C-BTC-80000-091026"
PUT = "P-BTC-80000-091026"


class FakeSymbols:
    """Stands in for SymbolsClient. `fail=True` makes every subscribe raise MarketDataError."""

    def __init__(self, fail: bool = False):
        self.fail = fail
        self.subscribed: list[str] = []

    def subscribe(self, symbol: str) -> bool:
        if self.fail:
            raise MarketDataError("market-data-service unreachable: ConnectError()")
        self.subscribed.append(symbol)
        return True


def tick_bytes(time: datetime, symbol: str = CALL, best_bid=5121.0, best_ask=5177.0, **extra) -> bytes:
    """A market-data-service TickerPayload as it arrives on Kafka."""
    return json.dumps({
        "symbol": symbol, "product_id": 153512, "strike_price": 80000.0, "time": time.isoformat(),
        "spot_price": 84330.4, "mark_price": 5148.09436923,
        "best_bid": best_bid, "best_ask": best_ask, "delta": 0.77664966, **extra,
    }).encode()


class FakeKafkaError:
    def __init__(self, code: int, text: str = "boom"):
        self._code, self._text = code, text

    def code(self) -> int:
        return self._code

    def __str__(self) -> str:
        return self._text


class FakeMessage:
    def __init__(self, value: Optional[bytes] = None, key: Optional[bytes] = None,
                 error: Optional[FakeKafkaError] = None, offset: int = 0,
                 headers: Optional[list[tuple[str, bytes]]] = None):
        self._value, self._key, self._error, self._offset = value, key, error, offset
        self._headers = headers

    def value(self): return self._value
    def key(self): return self._key
    def error(self): return self._error
    def topic(self): return "market-data.ticker"
    def partition(self): return 0
    def offset(self): return self._offset
    def headers(self): return self._headers

    @classmethod
    def eof(cls) -> "FakeMessage":
        return cls(error=FakeKafkaError(KafkaError._PARTITION_EOF, "EOF"))


class FakeConsumer:
    """Replays `messages` from poll() (None entries simulate an idle poll), then calls `on_drained`,
    which a test uses to stop the updater so run() returns."""

    def __init__(self, messages: list[Optional[FakeMessage]], on_drained: Callable[[], None] = lambda: None):
        self.messages = list(messages)
        self.on_drained = on_drained
        self.subscriptions: list[list[str]] = []
        self.closed = False

    def subscribe(self, topics: list[str]):
        self.subscriptions.append(topics)

    def poll(self, timeout: float):
        if self.messages:
            return self.messages.pop(0)
        self.on_drained()
        return None

    def close(self):
        self.closed = True
