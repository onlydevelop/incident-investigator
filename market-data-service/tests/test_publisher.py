from confluent_kafka import KafkaError, KafkaException

from delta_ticker import TickerPayload, TickerPublisher

from test_payload import MESSAGE


class FakeMessage:
    def __init__(self, topic, key):
        self._topic, self._key = topic, key

    def topic(self):
        return self._topic

    def key(self):
        return self._key


class FakeProducer:
    def __init__(self, raises=None, delivery_error=None, undelivered=0):
        self.raises = raises
        self.delivery_error = delivery_error
        self.undelivered = undelivered
        self.produced = []
        self.headers = []
        self.polls = []
        self.pending = []

    def produce(self, topic, key=None, value=None, headers=None, on_delivery=None):
        if self.raises:
            raise self.raises
        self.produced.append((topic, key, value))
        self.headers.append(dict(headers or []))
        self.pending.append((on_delivery, FakeMessage(topic, key)))

    def poll(self, timeout):
        self.polls.append(timeout)
        for on_delivery, msg in self.pending:
            on_delivery(self.delivery_error, msg)
        self.pending = []

    def flush(self, timeout):
        return self.undelivered


def payload():
    return TickerPayload.from_message(MESSAGE)


def test_publish_sends_json_keyed_by_symbol():
    fake = FakeProducer()
    p = payload()

    TickerPublisher(fake, topic="market-data.ticker").publish(p)

    assert fake.produced == [("market-data.ticker", b"C-BTC-79500-250926", p.to_json())]


def test_publish_polls_without_blocking():
    fake = FakeProducer()

    TickerPublisher(fake).publish(payload())

    assert fake.polls == [0]


def test_successful_delivery_logs_nothing(caplog):
    TickerPublisher(FakeProducer()).publish(payload())

    assert caplog.records == []


def test_delivery_failure_is_logged(caplog):
    fake = FakeProducer(delivery_error=KafkaError(KafkaError._MSG_TIMED_OUT))

    TickerPublisher(fake, topic="market-data.ticker").publish(payload())

    [record] = caplog.records
    assert "Failed to publish C-BTC-79500-250926 to market-data.ticker" in record.getMessage()
    assert (record.event, record.symbol, record.reason) == ("publish_failed", "C-BTC-79500-250926", "delivery")


def test_full_queue_is_logged_not_raised(caplog):
    TickerPublisher(FakeProducer(raises=BufferError())).publish(payload())

    assert "Failed to publish C-BTC-79500-250926: producer queue full" in caplog.text
    assert caplog.records[0].reason == "queue_full"


def test_kafka_exception_is_logged_not_raised(caplog):
    fake = FakeProducer(raises=KafkaException(KafkaError(KafkaError._TRANSPORT)))

    TickerPublisher(fake).publish(payload())

    assert "Failed to publish C-BTC-79500-250926" in caplog.text


def test_flush_returns_undelivered_count():
    assert TickerPublisher(FakeProducer(undelivered=3)).flush() == 3


def test_from_config_builds_a_real_producer_without_connecting():
    # librdkafka connects lazily, so this works with no broker running.
    publisher = TickerPublisher.from_config("localhost:1", topic="t")

    assert publisher.topic == "t"
    assert publisher.flush(0) == 0


def test_publish_carries_the_trace_context_in_headers(tracing):
    fake = FakeProducer()

    with tracing.start_as_current_span("tick") as tick:
        TickerPublisher(fake).publish(payload())

    traceparent = fake.headers[0]["traceparent"]
    assert traceparent.split("-")[1] == format(tick.get_span_context().trace_id, "032x")
