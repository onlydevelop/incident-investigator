import json
import logging
import threading
import time
from typing import Callable, Iterable, Optional

import websocket
from opentelemetry import trace
from opentelemetry.metrics import CallbackOptions, Observation

from delta_ticker.config import WEBSOCKET_URL
from delta_ticker.payload import TickerPayload
from delta_ticker.telemetry import meter, tracer

log = logging.getLogger(__name__)

ws_connected = meter.create_gauge("md_ws_connected", description="1 while the Delta websocket is open")
ticks_received = meter.create_counter("md_ticks_received", description="v2/ticker updates received")

# Exchange time (epoch seconds) of the latest tick per subscribed symbol. md_tick_age_seconds is
# computed from it at collection time, so it keeps growing when a feed stops, which is the point.
_latest_tick: dict[str, float] = {}


def _tick_ages(options: CallbackOptions) -> Iterable[Observation]:
    now = time.time()
    return [Observation(now - ts, {"symbol": symbol}) for symbol, ts in list(_latest_tick.items())]


meter.create_observable_gauge(
    "md_tick_age", callbacks=[_tick_ages], unit="s",
    description="Now minus the exchange timestamp of the latest tick, per symbol",
)


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
        ws_connected.set(0)

    @staticmethod
    def print_payload(payload: TickerPayload):
        """Logs the payload at debug level (LOG_LEVEL=DEBUG); metrics count ticks, not logs."""
        log.debug("tick", extra={"event": "tick", **payload.to_dict()})

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
            for symbol in removed:
                _latest_tick.pop(symbol, None)
            log.info(f"Symbols changed: +{added} -{removed}",
                     extra={"event": "symbols_changed", "added": added, "removed": removed})
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
        log.info("Socket opened", extra={"event": "ws_open"})
        ws_connected.set(1)
        with self._lock:
            self._connected = True
            # An empty subscribe stops all updates, so wait for symbols instead.
            if self.symbols:
                self._send("subscribe", self.symbols)
            else:
                log.info("No symbols configured yet; waiting for the next refresh", extra={"event": "no_symbols"})

    def _on_message(self, ws, message):
        # One trace per tick: the Redis write and the Kafka publish are its children, and the
        # position-updater's processing continues it through the Kafka message headers.
        with tracer.start_as_current_span(f"{self.CHANNEL} process", kind=trace.SpanKind.CONSUMER) as span:
            try:
                data = json.loads(message)
                if data.get("type") != self.CHANNEL:
                    log.info("Websocket message", extra={"event": "ws_message", "data": data})
                    return
                payload = TickerPayload.from_message(data)
            except json.JSONDecodeError as e:
                log.warning(f"Failed to decode message: {e}", extra={"event": "bad_message", "reason": "json"})
                span.set_status(trace.StatusCode.ERROR, "undecodable message")
                return
            except (KeyError, TypeError, ValueError) as e:
                log.warning(f"Failed to build payload: {e!r} from {message[:200]}",
                            extra={"event": "bad_message", "reason": "payload"})
                span.set_status(trace.StatusCode.ERROR, "bad payload")
                return
            span.set_attribute("symbol", payload.symbol)
            ticks_received.add(1)
            _latest_tick[payload.symbol] = data["timestamp"] / 1_000_000
            self.on_payload(payload)

    def _on_error(self, ws, error):
        log.error(f"Socket Error: {error!r}", extra={"event": "ws_error"})

    def _on_close(self, ws, close_status_code, close_msg):
        with self._lock:
            self._connected = False
        ws_connected.set(0)
        log.warning(f"Socket closed with status: {close_status_code} and message: {close_msg}",
                    extra={"event": "ws_closed", "status": close_status_code})
