import logging
from decimal import Decimal

import psycopg
import pytest
from confluent_kafka import KafkaError
from opentelemetry.trace import StatusCode

from order_service import PositionUpdater, Side, Status
from order_service import updater as updater_module

from fakes import CALL, FakeConsumer, FakeKafkaError, FakeMessage, tick_bytes


class TestHandle:
    def test_opens_then_updates(self, updater, store, make_position, later):
        p = make_position(side=Side.BUY)

        assert updater.handle(tick_bytes(later(1))) == (1, 0)
        assert updater.handle(tick_bytes(later(2), best_bid=5200.1, best_ask=5250.0)) == (0, 1)

        p = store.get(p.id)
        assert p.status == Status.OPEN
        assert p.entry_price == Decimal("5177.0")
        # Floats are converted via str, so there's no binary-float noise in the numeric column.
        assert p.current_price == Decimal("5200.1")
        assert p.entry_time == later(1)

    def test_accepts_null_quotes(self, updater, make_position, later):
        make_position(side=Side.BUY)

        assert updater.handle(tick_bytes(later(1), best_ask=None)) == (0, 0)

    def test_logs_openings(self, updater, make_position, later, caplog):
        caplog.set_level(logging.INFO)
        make_position(side=Side.SELL)

        updater.handle(tick_bytes(later(1)))

        [record] = caplog.records
        assert f"Opened 1 position(s) in {CALL} at bid=5121.0 ask=5177.0" == record.getMessage()
        assert (record.event, record.symbol, record.opened) == ("positions_opened", CALL, 1)

    def test_quiet_when_nothing_opens(self, updater, later, caplog):
        caplog.set_level(logging.INFO)
        updater.handle(tick_bytes(later(1)))

        assert caplog.records == []

    def test_records_tick_age(self, updater, later, metric_points):
        before = metric_points().get("position_updater_tick_age", [({}, 0)])[0][1]

        updater.handle(tick_bytes(later(-3)))

        [(_, count)] = metric_points()["position_updater_tick_age"]
        assert count == before + 1

    @pytest.mark.parametrize("value", [
        pytest.param(b"not json", id="not-json"),
        pytest.param(b'{"time": "2026-09-25T14:00:00+05:30"}', id="no-symbol"),
        pytest.param(b'{"symbol": "X", "time": "yesterday"}', id="bad-time"),
    ])
    def test_rejects_bad_messages(self, updater, value):
        with pytest.raises((ValueError, KeyError)):
            updater.handle(value)


class TestRun:
    """The poll loop, driven by a FakeConsumer that stops the updater once it runs dry."""

    @pytest.fixture
    def run(self, store):
        def run(messages, store_=None):
            updater = PositionUpdater(store_ or store)
            consumer = FakeConsumer(messages, on_drained=updater.stop)
            updater.consumer = consumer
            updater.run()
            return consumer
        return run

    def test_subscribes_applies_ticks_and_closes(self, run, store, make_position, later):
        p = make_position()

        consumer = run([None, FakeMessage(tick_bytes(later(1))), FakeMessage(tick_bytes(later(2)))])

        assert consumer.subscriptions == [["market-data.ticker"]]
        assert consumer.closed
        p = store.get(p.id)
        assert p.status == Status.OPEN and p.current_price == Decimal("5121.0")

    def test_bad_message_is_skipped_and_loop_continues(self, run, store, make_position, later, caplog):
        p = make_position()

        run([FakeMessage(b"garbage", offset=7), FakeMessage(tick_bytes(later(1)))])

        assert "Skipping bad message at market-data.ticker[0]@7" in caplog.text
        assert store.get(p.id).status == Status.OPEN

    def test_partition_eof_is_silent_other_errors_are_logged(self, run, caplog):
        run([FakeMessage.eof(), FakeMessage(error=FakeKafkaError(KafkaError._TRANSPORT, "broker down"))])

        assert "EOF" not in caplog.text
        assert "Kafka error: broker down" in caplog.text

    def test_postgres_outage_is_logged_not_raised(self, run, later, caplog, tracing):
        class DownStore:
            def apply_tick(self, *args, **kwargs):
                raise psycopg.OperationalError("connection refused")

        consumer = run([FakeMessage(tick_bytes(later(1)), key=CALL.encode())], store_=DownStore())

        assert f"Failed to apply tick for b'{CALL}'" in caplog.text
        assert caplog.records[-1].reason == "postgres_unavailable"
        assert consumer.closed
        [span] = tracing.spans()
        assert span.status.status_code == StatusCode.ERROR

    def test_processing_continues_the_producers_trace(self, run, store, later, tracing):
        trace_id, parent_id = "0af7651916cd43dd8448eb211c80319c", "b7ad6b7169203331"
        headers = [("traceparent", f"00-{trace_id}-{parent_id}-01".encode())]

        run([FakeMessage(tick_bytes(later(1)), offset=3, headers=headers)])

        [span] = tracing.spans()
        assert span.name == "market-data.ticker process"
        assert format(span.context.trace_id, "032x") == trace_id
        assert format(span.parent.span_id, "016x") == parent_id
        assert span.attributes["messaging.kafka.offset"] == 3

    def test_message_without_headers_starts_its_own_trace(self, run, store, later, tracing):
        run([FakeMessage(tick_bytes(later(1)))])

        [span] = tracing.spans()
        assert span.parent is None

    def test_consumer_is_closed_even_if_handling_crashes(self, later):
        class BrokenStore:
            def apply_tick(self, *args, **kwargs):
                raise RuntimeError("bug")

        updater = PositionUpdater(BrokenStore())
        updater.consumer = FakeConsumer([FakeMessage(tick_bytes(later(1)))])

        with pytest.raises(RuntimeError):
            updater.run()
        assert updater.consumer.closed


class TestWiring:
    def test_new_consumer_config(self, monkeypatch):
        seen = {}
        monkeypatch.setattr(updater_module, "Consumer", lambda config: seen.update(config) or "consumer")

        assert PositionUpdater.new_consumer("kafka:9092", "group-x") == "consumer"
        assert seen["bootstrap.servers"] == "kafka:9092"
        assert seen["group.id"] == "group-x"
        assert seen["auto.offset.reset"] == "latest"

    def test_run_creates_a_consumer_when_none_given(self, monkeypatch):
        updater = PositionUpdater(store=None)
        monkeypatch.setattr(PositionUpdater, "new_consumer", staticmethod(lambda: FakeConsumer([], updater.stop)))

        updater.run()

        assert updater.consumer.closed

    def test_main_wires_signals_and_closes_the_store(self, monkeypatch):
        class Store:
            closed = False

            def close(self):
                self.closed = True

        store, handlers = Store(), {}
        monkeypatch.setattr(updater_module.PositionStore, "from_url", classmethod(lambda cls: store))
        monkeypatch.setattr(updater_module.signal, "signal", lambda sig, handler: handlers.__setitem__(sig, handler))

        def fake_run(self):
            # SIGTERM from `docker stop` must end the loop.
            handlers[updater_module.signal.SIGTERM](None, None)
            assert self._running is False

        monkeypatch.setattr(PositionUpdater, "run", fake_run)

        updater_module.main()

        assert set(handlers) == {updater_module.signal.SIGTERM, updater_module.signal.SIGINT}
        assert store.closed
