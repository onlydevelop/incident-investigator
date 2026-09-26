import logging

from delta_ticker import telemetry
from delta_ticker.cache import TickerCache
from delta_ticker.client import DeltaTickerClient
from delta_ticker.config import CACHE_TTL_SECONDS, REDIS_URL, SYMBOL_REFRESH_SECONDS
from delta_ticker.payload import TickerPayload
from delta_ticker.publisher import TickerPublisher
from delta_ticker.symbols import SymbolRefresher, SymbolRegistry

log = logging.getLogger("delta_ticker")


def main():
    telemetry.setup("delta-ticker")
    # Imported here: only the running ticker traces its Redis calls.
    from opentelemetry.instrumentation.redis import RedisInstrumentor
    RedisInstrumentor().instrument()

    cache = TickerCache.from_url(REDIS_URL, CACHE_TTL_SECONDS)
    registry = SymbolRegistry.from_url(REDIS_URL)
    publisher = TickerPublisher.from_config()

    def on_payload(payload: TickerPayload):
        cache.store(payload)
        publisher.publish(payload)
        DeltaTickerClient.print_payload(payload)

    symbols = registry.get() or []
    log.info(f"Loaded symbols from {SymbolRegistry.KEY}: {symbols}", extra={"event": "symbols_loaded", "symbols": symbols})
    log.info(f"Publishing to Kafka topic {publisher.topic}", extra={"event": "startup", "topic": publisher.topic})
    client = DeltaTickerClient(symbols, on_payload=on_payload)

    refresher = SymbolRefresher(registry, client.update_symbols, SYMBOL_REFRESH_SECONDS)
    refresher.start()
    try:
        client.run()
    finally:
        refresher.stop()
        undelivered = publisher.flush()
        if undelivered:
            log.error(f"{undelivered} updates were not delivered to Kafka",
                      extra={"event": "shutdown_undelivered", "undelivered": undelivered})


if __name__ == "__main__":
    main()
