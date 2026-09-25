import websocket
import json
import threading
from typing import Callable, Optional

from delta_ticker.config import WEBSOCKET_URL
from delta_ticker.payload import TickerPayload


class DeltaTickerClient:
    """Subscribes to Delta's v2/ticker channel and hands each update to `on_payload`."""

    CHANNEL = "v2/ticker"

    def __init__(
        self,
        symbols: list[str],
        on_payload: Optional[Callable[[TickerPayload], None]] = None,
        url: str = WEBSOCKET_URL,
    ):
        self.symbols = sorted(set(symbols))
        self.on_payload = on_payload or self.print_payload
        self.url = url
        self.ws: Optional[websocket.WebSocketApp] = None
        self._connected = False
        # update_symbols() runs on the refresher thread, the callbacks on the socket thread.
        self._lock = threading.Lock()

    @staticmethod
    def print_payload(payload: TickerPayload):
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

    def update_symbols(self, symbols: list[str]):
        """Switch to a new symbol list. On a live socket only the difference is sent:
        Delta's subscribe adds to existing subscriptions and unsubscribe removes them."""
        with self._lock:
            new, old = set(symbols), set(self.symbols)
            added, removed = sorted(new - old), sorted(old - new)
            self.symbols = sorted(new)
            if not (added or removed):
                return
            print(f"Symbols changed: +{added} -{removed}")
            if self._connected:
                if removed:
                    self._send("unsubscribe", removed)
                if added:
                    self._send("subscribe", added)

    def _send(self, action: str, symbols: list[str]):
        payload = {
            "type": action,
            "payload": {
                "channels": [
                    {
                        "name": self.CHANNEL,
                        "symbols": symbols
                    }
                ]
            }
        }
        self.ws.send(json.dumps(payload))

    def _on_open(self, ws):
        print("Socket opened")
        with self._lock:
            self._connected = True
            # An empty subscribe stops all updates, so wait for symbols instead.
            if self.symbols:
                self._send("subscribe", self.symbols)
            else:
                print("No symbols configured yet; waiting for the next refresh")

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
        with self._lock:
            self._connected = False
        print(f"Socket closed with status: {close_status_code} and message: {close_msg}")
