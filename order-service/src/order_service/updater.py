"""position-updater: prices positions from market-data-service's Kafka ticker feed.

For each tick on `market-data.ticker`, pending positions in that symbol created at or before the
tick are opened at their entry price (ask for a buy, bid for a sell), and open positions get a
new current_price (bid for a buy, ask for a sell).
"""
import json
import logging
import signal
import time
from datetime import datetime
from decimal import Decimal
from typing import Optional

import psycopg
from confluent_kafka import Consumer, KafkaError, Message
from opentelemetry import propagate, trace

from order_service import telemetry
from order_service.config import CONSUMER_GROUP, KAFKA_BOOTSTRAP_SERVERS, TICKER_TOPIC
from order_service.store import PositionStore
from order_service.telemetry import meter, tracer

log = logging.getLogger(__name__)

ticks = meter.create_counter(
    "position_updater_ticks", description="Ticks consumed, by result (applied, bad_message, db_error)"
)
positions_opened = meter.create_counter("positions_opened", description="Pending positions opened by a tick")
tick_age = meter.create_histogram(
    "position_updater_tick_age", unit="s",
    description="Exchange timestamp to processing here: how stale prices are when positions see them",
    explicit_bucket_boundaries_advisory=[0.05, 0.1, 0.25, 0.5, 1, 2, 5, 10, 30, 60],
)


def _to_decimal(value) -> Optional[Decimal]:
    # Via str, so 5121.3 becomes Decimal("5121.3") rather than its binary float expansion.
    return None if value is None else Decimal(str(value))


class PositionUpdater:
    def __init__(self, store: PositionStore, consumer: Optional[Consumer] = None, topic: str = TICKER_TOPIC):
        self.store = store
        self.consumer = consumer
        self.topic = topic
        self._running = False

    @staticmethod
    def new_consumer(bootstrap_servers: str = KAFKA_BOOTSTRAP_SERVERS, group_id: str = CONSUMER_GROUP) -> Consumer:
        return Consumer({
            "bootstrap.servers": bootstrap_servers,
            "group.id": group_id,
            "client.id": "position-updater",
            # A new group starts from live prices; after a restart it resumes from its committed offset.
            # Replayed ticks are harmless: apply_tick ignores anything older than a position's prices.
            "auto.offset.reset": "latest",
            # librdkafka's own messages (broker unreachable, rebalances) as JSON log lines, not raw stderr.
            "logger": logging.getLogger("librdkafka"),
        })

    def handle(self, value: bytes) -> tuple[int, int]:
        """Applies one ticker payload (market-data-service's TickerPayload JSON); returns (opened, updated)."""
        tick = json.loads(value)
        symbol = tick["symbol"]
        tick_time = datetime.fromisoformat(tick["time"])
        opened, updated = self.store.apply_tick(
            symbol,
            tick_time,
            bid=_to_decimal(tick.get("best_bid")),
            ask=_to_decimal(tick.get("best_ask")),
        )
        # Clamped: a histogram can't take negative values, which clock skew between the exchange and
        # this node would produce.
        tick_age.record(max(0.0, time.time() - tick_time.timestamp()))
        if opened:
            positions_opened.add(opened)
            log.info(f"Opened {opened} position(s) in {symbol} at bid={tick.get('best_bid')} ask={tick.get('best_ask')}",
                     extra={"event": "positions_opened", "symbol": symbol, "opened": opened,
                            "bid": tick.get("best_bid"), "ask": tick.get("best_ask")})
        return opened, updated

    def process(self, msg: Message):
        """Handles one message in a span that continues the tick's trace from its `traceparent`
        header, so the delta-ticker's publish and this processing are one trace in Tempo."""
        parent = propagate.extract({k: v.decode() for k, v in msg.headers() or [] if v is not None})
        attributes = {
            "messaging.system": "kafka",
            "messaging.operation.type": "process",
            "messaging.destination.name": msg.topic(),
            "messaging.consumer.group.name": CONSUMER_GROUP,
            "messaging.destination.partition.id": str(msg.partition()),
            "messaging.kafka.offset": msg.offset(),
        }
        with tracer.start_as_current_span(f"{msg.topic()} process", context=parent, kind=trace.SpanKind.CONSUMER,
                                          attributes=attributes) as span:
            try:
                self.handle(msg.value())
                ticks.add(1, {"result": "applied"})
            except (json.JSONDecodeError, KeyError, TypeError, ValueError) as e:
                ticks.add(1, {"result": "bad_message"})
                span.set_status(trace.StatusCode.ERROR, "bad message")
                log.warning(f"Skipping bad message at {msg.topic()}[{msg.partition()}]@{msg.offset()}: {e!r}",
                            extra={"event": "bad_message", "partition": msg.partition(), "offset": msg.offset()})
            except psycopg.OperationalError as e:
                # Postgres is down; this tick is lost, and the next one for the symbol catches up.
                ticks.add(1, {"result": "db_error"})
                span.record_exception(e)
                span.set_status(trace.StatusCode.ERROR, "postgres unavailable")
                key = msg.key().decode() if msg.key() else None
                log.error(f"Failed to apply tick for {msg.key()!r}: {e!r}",
                          extra={"event": "tick_failed", "symbol": key, "reason": "postgres_unavailable"})

    def run(self):
        """Consumes until stop() is called."""
        self.consumer = self.consumer or self.new_consumer()
        self.consumer.subscribe([self.topic])
        log.info(f"Consuming {self.topic} as {CONSUMER_GROUP}",
                 extra={"event": "startup", "topic": self.topic, "consumer_group": CONSUMER_GROUP})
        self._running = True
        try:
            while self._running:
                msg = self.consumer.poll(1.0)
                if msg is None:
                    continue
                if msg.error():
                    # Partition EOF and transient broker errors are informational; the client retries.
                    if msg.error().code() != KafkaError._PARTITION_EOF:
                        log.warning(f"Kafka error: {msg.error()}", extra={"event": "kafka_error"})
                    continue
                self.process(msg)
        finally:
            # Commits final offsets and leaves the group, so a restart rebalances straight away.
            self.consumer.close()

    def stop(self):
        self._running = False


def main():
    telemetry.setup("position-updater")
    from opentelemetry.instrumentation.psycopg import PsycopgInstrumentor
    PsycopgInstrumentor().instrument()
    store = PositionStore.from_url()
    updater = PositionUpdater(store)
    signal.signal(signal.SIGTERM, lambda *_: updater.stop())
    signal.signal(signal.SIGINT, lambda *_: updater.stop())
    try:
        updater.run()
    finally:
        store.close()


if __name__ == "__main__":
    main()
