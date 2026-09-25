import websocket
import json
from dataclasses import dataclass, asdict
from typing import Optional

# Public websocket endpoint (no authentication required for ticker channel)
WEBSOCKET_URL = "wss://socket.india.delta.exchange"

# Replace with your list of option symbols, or use an option-chain shorthand like "BTC-150426"
OPTION_SYMBOLS = [
    "C-BTC-79500-250926",
    "P-BTC-79500-250926",
]


def _to_float(value) -> Optional[float]:
    """Delta sends most numbers as strings; missing values come as None."""
    if value is None or value == "":
        return None
    return float(value)


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
            strike_price=_to_float(data.get("strike_price")),
            timestamp_us=data["timestamp"],
            spot_price=_to_float(data.get("spot_price")),
            mark_price=_to_float(data.get("mark_price")),
            best_bid=_to_float(quotes.get("best_bid")),
            bid_size=_to_float(quotes.get("bid_size")),
            best_ask=_to_float(quotes.get("best_ask")),
            ask_size=_to_float(quotes.get("ask_size")),
            bid_iv=_to_float(quotes.get("bid_iv")),
            ask_iv=_to_float(quotes.get("ask_iv")),
            mark_iv=_to_float(quotes.get("mark_iv")),
            delta=_to_float(greeks.get("delta")),
            gamma=_to_float(greeks.get("gamma")),
            rho=_to_float(greeks.get("rho")),
            theta=_to_float(greeks.get("theta")),
            vega=_to_float(greeks.get("vega")),
            open_interest=_to_float(data.get("oi")),
            volume=_to_float(data.get("volume")),
        )

    def key(self) -> bytes:
        """Kafka message key: partition by symbol so updates per instrument stay ordered."""
        return self.symbol.encode("utf-8")

    def to_dict(self) -> dict:
        return asdict(self)

    def to_json(self) -> bytes:
        """Kafka message value."""
        return json.dumps(self.to_dict(), separators=(",", ":")).encode("utf-8")


def on_open(ws):
    print("Socket opened")
    subscribe(ws, "v2/ticker", OPTION_SYMBOLS)

def subscribe(ws, channel, symbols):
    payload = {
        "type": "subscribe",
        "payload": {
            "channels": [
                {
                    "name": channel,
                    "symbols": symbols
                }
            ]
        }
    }
    ws.send(json.dumps(payload))

def handle_payload(payload: TickerPayload):
    # Swap this for a Kafka producer later, e.g.
    # producer.produce("market-data.ticker", key=payload.key(), value=payload.to_json())
    print(payload.to_json().decode("utf-8"))

def on_message(ws, message):
    try:
        data = json.loads(message)
        if data.get("type") == "v2/ticker":
            handle_payload(TickerPayload.from_message(data))
        else:
            print(json.dumps(data, indent=2))
    except json.JSONDecodeError as e:
        print(f"Failed to decode message: {e}")
    except (KeyError, TypeError, ValueError) as e:
        print(f"Failed to build payload: {e!r} from {message[:200]}")

def on_error(ws, error):
    print(f"Socket Error: {error!r}")

def on_close(ws, close_status_code, close_msg):
    print(f"Socket closed with status: {close_status_code} and message: {close_msg}")

if __name__ == "__main__":
    ws = websocket.WebSocketApp(
        WEBSOCKET_URL,
        on_open=on_open,
        on_message=on_message,
        on_error=on_error,
        on_close=on_close
    )
    ws.run_forever()  # runs indefinitely, reconnect logic can be added if needed
