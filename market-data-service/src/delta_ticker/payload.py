import json
from dataclasses import dataclass, asdict
from typing import Optional


@dataclass(frozen=True)
class TickerPayload:
    """One v2/ticker update, flattened into a Kafka-ready record."""
    source: str
    symbol: str
    product_id: int
    contract_type: str
    underlying: str
    strike_price: Optional[float]
    timestamp_us: int  # exchange timestamp, microseconds since epoch
    spot_price: Optional[float]
    mark_price: Optional[float]
    best_bid: Optional[float]
    bid_size: Optional[float]
    best_ask: Optional[float]
    ask_size: Optional[float]
    bid_iv: Optional[float]
    ask_iv: Optional[float]
    mark_iv: Optional[float]
    delta: Optional[float]
    gamma: Optional[float]
    rho: Optional[float]
    theta: Optional[float]
    vega: Optional[float]
    open_interest: Optional[float]
    volume: Optional[float]

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
            source="delta.exchange",
            symbol=data["symbol"],
            product_id=data["product_id"],
            contract_type=data.get("contract_type"),
            underlying=data.get("underlying_asset_symbol"),
            strike_price=cls._to_float(data.get("strike_price")),
            timestamp_us=data["timestamp"],
            spot_price=cls._to_float(data.get("spot_price")),
            mark_price=cls._to_float(data.get("mark_price")),
            best_bid=cls._to_float(quotes.get("best_bid")),
            bid_size=cls._to_float(quotes.get("bid_size")),
            best_ask=cls._to_float(quotes.get("best_ask")),
            ask_size=cls._to_float(quotes.get("ask_size")),
            bid_iv=cls._to_float(quotes.get("bid_iv")),
            ask_iv=cls._to_float(quotes.get("ask_iv")),
            mark_iv=cls._to_float(quotes.get("mark_iv")),
            delta=cls._to_float(greeks.get("delta")),
            gamma=cls._to_float(greeks.get("gamma")),
            rho=cls._to_float(greeks.get("rho")),
            theta=cls._to_float(greeks.get("theta")),
            vega=cls._to_float(greeks.get("vega")),
            open_interest=cls._to_float(data.get("oi")),
            volume=cls._to_float(data.get("volume")),
        )

    @classmethod
    def from_json(cls, raw: bytes | str) -> "TickerPayload":
        """Inverse of to_json()."""
        return cls(**json.loads(raw))

    def key(self) -> bytes:
        """Kafka message key: partition by symbol so updates per instrument stay ordered."""
        return self.symbol.encode("utf-8")

    def to_dict(self) -> dict:
        return asdict(self)

    def to_json(self) -> bytes:
        """Kafka message value."""
        return json.dumps(self.to_dict(), separators=(",", ":")).encode("utf-8")
