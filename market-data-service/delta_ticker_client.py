import websocket
import json
from typing import Callable, Optional

from ticker_payload import TickerPayload

# Public websocket endpoint (no authentication required for ticker channel)
WEBSOCKET_URL = "wss://socket.india.delta.exchange"


class DeltaTickerClient:
    """Subscribes to Delta's v2/ticker channel and hands each update to `on_payload`."""

    CHANNEL = "v2/ticker"

    def __init__(
        self,
        symbols: list[str],
        on_payload: Optional[Callable[[TickerPayload], None]] = None,
        url: str = WEBSOCKET_URL,
    ):
        self.symbols = symbols
        self.on_payload = on_payload or self.print_payload
        self.url = url
        self.ws: Optional[websocket.WebSocketApp] = None

    @staticmethod
    def print_payload(payload: TickerPayload):
        # Swap this for a Kafka producer later, e.g.
        # producer.produce("market-data.ticker", key=payload.key(), value=payload.to_json())
        print(payload.to_json().decode("utf-8"))

    def run(self):
        """Blocks until the socket closes; reconnect logic can be added if needed."""
        self.ws = websocket.WebSocketApp(
            self.url,
            on_open=self._on_open,
            on_message=self._on_message,
            on_error=self._on_error,
            on_close=self._on_close,
        )
        self.ws.run_forever()

    def close(self):
        if self.ws:
            self.ws.close()

    def subscribe(self, ws, channel: str, symbols: list[str]):
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

    def _on_open(self, ws):
        print("Socket opened")
        self.subscribe(ws, self.CHANNEL, self.symbols)

    def _on_message(self, ws, message):
        try:
            data = json.loads(message)
            if data.get("type") == self.CHANNEL:
                self.on_payload(TickerPayload.from_message(data))
            else:
                print(json.dumps(data, indent=2))
        except json.JSONDecodeError as e:
            print(f"Failed to decode message: {e}")
        except (KeyError, TypeError, ValueError) as e:
            print(f"Failed to build payload: {e!r} from {message[:200]}")

    def _on_error(self, ws, error):
        print(f"Socket Error: {error!r}")

    def _on_close(self, ws, close_status_code, close_msg):
        print(f"Socket closed with status: {close_status_code} and message: {close_msg}")
