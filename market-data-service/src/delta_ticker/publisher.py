import logging

from confluent_kafka import KafkaError, KafkaException, Message, Producer
from opentelemetry import propagate, trace

from delta_ticker.config import KAFKA_BOOTSTRAP_SERVERS, KAFKA_MESSAGE_TIMEOUT_MS, TICKER_TOPIC
from delta_ticker.payload import TickerPayload
from delta_ticker.telemetry import meter, tracer

log = logging.getLogger(__name__)

ticks_published = meter.create_counter("md_ticks_published", description="Ticks Kafka acknowledged")
produce_errors = meter.create_counter(
    "md_kafka_produce_errors", description="Ticks not delivered to Kafka, by reason (queue_full, produce, delivery)"
)


class TickerPublisher:
    """Publishes each TickerPayload to Kafka, keyed by symbol so updates per instrument stay ordered.

    Sends are asynchronous; librdkafka batches and retries in the background. Failures are logged,
    not raised, so a Kafka outage doesn't take down the websocket feed.

    Each message carries the current trace context in a `traceparent` header, so the consumer's
    processing continues the tick's trace.
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
            # librdkafka's own messages (broker unreachable, ...) as JSON log lines, not raw stderr.
            "logger": logging.getLogger("librdkafka"),
        })
        return cls(producer, topic)

    def publish(self, payload: TickerPayload):
        attributes = {
            "messaging.system": "kafka",
            "messaging.operation.type": "send",
            "messaging.destination.name": self.topic,
            "messaging.kafka.message.key": payload.symbol,
        }
        with tracer.start_as_current_span(f"{self.topic} publish", kind=trace.SpanKind.PRODUCER,
                                          attributes=attributes) as span:
            headers: dict[str, str] = {}
            propagate.inject(headers)
            try:
                self.producer.produce(
                    self.topic,
                    key=payload.key(),
                    value=payload.to_json(),
                    headers=list(headers.items()),
                    on_delivery=self._on_delivery,
                )
            except BufferError:
                # The local queue is full, most likely because Kafka has been unreachable for a while.
                self._failed(span, payload.symbol, "queue_full", f"Failed to publish {payload.symbol}: producer queue full")
            except KafkaException as e:
                self._failed(span, payload.symbol, "produce", f"Failed to publish {payload.symbol}: {e!r}")
        # Serves delivery callbacks from earlier sends without blocking.
        self.producer.poll(0)

    def flush(self, timeout: float = 5.0) -> int:
        """Waits up to `timeout` seconds for queued messages; returns how many are still undelivered."""
        return self.producer.flush(timeout)

    @staticmethod
    def _failed(span: trace.Span, symbol: str, reason: str, message: str):
        produce_errors.add(1, {"reason": reason})
        span.set_status(trace.StatusCode.ERROR, reason)
        log.error(message, extra={"event": "publish_failed", "symbol": symbol, "reason": reason})

    @staticmethod
    def _on_delivery(err: KafkaError | None, msg: Message):
        if err is None:
            ticks_published.add(1)
            return
        # Runs later, from poll(), outside the tick's span; the log line says which tick it was.
        key = msg.key().decode("utf-8") if msg.key() else None
        produce_errors.add(1, {"reason": "delivery"})
        log.error(f"Failed to publish {key} to {msg.topic()}: {err}",
                  extra={"event": "publish_failed", "symbol": key, "reason": "delivery", "error": str(err)})
