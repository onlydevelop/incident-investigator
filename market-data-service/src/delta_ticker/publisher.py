from confluent_kafka import KafkaError, KafkaException, Message, Producer

from delta_ticker.config import KAFKA_BOOTSTRAP_SERVERS, KAFKA_MESSAGE_TIMEOUT_MS, TICKER_TOPIC
from delta_ticker.payload import TickerPayload


class TickerPublisher:
    """Publishes each TickerPayload to Kafka, keyed by symbol so updates per instrument stay ordered.

    Sends are asynchronous; librdkafka batches and retries in the background. Failures are logged,
    not raised, so a Kafka outage doesn't take down the websocket feed.
    """

    def __init__(self, producer: Producer, topic: str = TICKER_TOPIC):
        self.producer = producer
        self.topic = topic

    @classmethod
    def from_config(
        cls,
        bootstrap_servers: str = KAFKA_BOOTSTRAP_SERVERS,
        topic: str = TICKER_TOPIC,
    ) -> "TickerPublisher":
        producer = Producer({
            "bootstrap.servers": bootstrap_servers,
            "client.id": "delta-ticker",
            # Retries can't reorder or duplicate messages within a partition.
            "enable.idempotence": True,
            "linger.ms": 5,
            "message.timeout.ms": KAFKA_MESSAGE_TIMEOUT_MS,
        })
        return cls(producer, topic)

    def publish(self, payload: TickerPayload):
        try:
            self.producer.produce(
                self.topic,
                key=payload.key(),
                value=payload.to_json(),
                on_delivery=self._on_delivery,
            )
        except BufferError:
            # The local queue is full, most likely because Kafka has been unreachable for a while.
            print(f"Failed to publish {payload.symbol}: producer queue full")
        except KafkaException as e:
            print(f"Failed to publish {payload.symbol}: {e!r}")
        # Serves delivery callbacks from earlier sends without blocking.
        self.producer.poll(0)

    def flush(self, timeout: float = 5.0) -> int:
        """Waits up to `timeout` seconds for queued messages; returns how many are still undelivered."""
        return self.producer.flush(timeout)

    @staticmethod
    def _on_delivery(err: KafkaError | None, msg: Message):
        if err is not None:
            key = msg.key().decode("utf-8") if msg.key() else None
            print(f"Failed to publish {key} to {msg.topic()}: {err}")
