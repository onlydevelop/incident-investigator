"""position-updater: prices positions from market-data-service's Kafka ticker feed.

For each tick on `market-data.ticker`, pending positions in that symbol created at or before the
tick are opened at their entry price (ask for a buy, bid for a sell), and open positions get a
new current_price (bid for a buy, ask for a sell).
"""
import json
import signal
from datetime import datetime
from decimal import Decimal
from typing import Optional

import psycopg
from confluent_kafka import Consumer, KafkaError

from order_service.config import CONSUMER_GROUP, KAFKA_BOOTSTRAP_SERVERS, TICKER_TOPIC
from order_service.store import PositionStore


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
        })

    def handle(self, value: bytes) -> tuple[int, int]:
        """Applies one ticker payload (market-data-service's TickerPayload JSON); returns (opened, updated)."""
        tick = json.loads(value)
        symbol = tick["symbol"]
        opened, updated = self.store.apply_tick(
            symbol,
            datetime.fromisoformat(tick["time"]),
            bid=_to_decimal(tick.get("best_bid")),
            ask=_to_decimal(tick.get("best_ask")),
        )
        if opened:
            print(f"Opened {opened} position(s) in {symbol} at bid={tick.get('best_bid')} ask={tick.get('best_ask')}")
        return opened, updated

    def run(self):
        """Consumes until stop() is called."""
        self.consumer = self.consumer or self.new_consumer()
        self.consumer.subscribe([self.topic])
        print(f"Consuming {self.topic} as {CONSUMER_GROUP}")
        self._running = True
        try:
            while self._running:
                msg = self.consumer.poll(1.0)
                if msg is None:
                    continue
                if msg.error():
                    # Partition EOF and transient broker errors are informational; the client retries.
                    if msg.error().code() != KafkaError._PARTITION_EOF:
                        print(f"Kafka error: {msg.error()}")
                    continue
                try:
                    self.handle(msg.value())
                except (json.JSONDecodeError, KeyError, TypeError, ValueError) as e:
                    print(f"Skipping bad message at {msg.topic()}[{msg.partition()}]@{msg.offset()}: {e!r}")
                except psycopg.OperationalError as e:
                    # Postgres is down; this tick is lost, and the next one for the symbol catches up.
                    print(f"Failed to apply tick for {msg.key()!r}: {e!r}")
        finally:
            # Commits final offsets and leaves the group, so a restart rebalances straight away.
            self.consumer.close()

    def stop(self):
        self._running = False


def main():
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
