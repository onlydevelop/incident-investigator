import json
from dataclasses import asdict, dataclass, fields
from typing import Optional


@dataclass(frozen=True)
class TickerPayload:
    """One v2/ticker update, flattened into a Kafka-ready record."""
    symbol: str
    product_id: int
    strike_price: Optional[float]
    timestamp_us: int  # exchange timestamp, microseconds since epoch
    spot_price: Optional[float]
    mark_price: Optional[float]
    best_bid: Optional[float]
    best_ask: Optional[float]
    delta: Optional[float]

    @staticmethod
    def _to_float(value) -> Optional[float]:
        """Delta sends most numbers as strings; missing values come as None."""
        if value is None or value == "":
            return None
        return float(value)

    @classmethod
    def from_message(cls, data: dict) -> "TickerPayload":
        quotes = data.get("quotes") or {}
        greeks = data.get("greeks") or {}
        return cls(
            symbol=data["symbol"],
            product_id=data["product_id"],
            strike_price=cls._to_float(data.get("strike_price")),
            timestamp_us=data["timestamp"],
            spot_price=cls._to_float(data.get("spot_price")),
            mark_price=cls._to_float(data.get("mark_price")),
            best_bid=cls._to_float(quotes.get("best_bid")),
            best_ask=cls._to_float(quotes.get("best_ask")),
            delta=cls._to_float(greeks.get("delta")),
        )

    @classmethod
    def from_json(cls, raw: bytes | str) -> "TickerPayload":
        """Inverse of to_json(). Unknown keys are ignored, so entries cached by an
        older version with more fields still load."""
        data = json.loads(raw)
        return cls(**{f.name: data.get(f.name) for f in fields(cls)})

    def key(self) -> bytes:
        """Kafka message key: partition by symbol so updates per instrument stay ordered."""
        return self.symbol.encode("utf-8")

    def to_dict(self) -> dict:
        return asdict(self)

    def to_json(self) -> bytes:
        """Kafka message value."""
        return json.dumps(self.to_dict(), separators=(",", ":")).encode("utf-8")
