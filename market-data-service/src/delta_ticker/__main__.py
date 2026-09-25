from delta_ticker.cache import TickerCache
from delta_ticker.client import DeltaTickerClient
from delta_ticker.config import CACHE_TTL_SECONDS, REDIS_URL, SYMBOL_REFRESH_SECONDS
from delta_ticker.payload import TickerPayload
from delta_ticker.publisher import TickerPublisher
from delta_ticker.symbols import SymbolRefresher, SymbolRegistry


def main():
    cache = TickerCache.from_url(REDIS_URL, CACHE_TTL_SECONDS)
    registry = SymbolRegistry.from_url(REDIS_URL)
    publisher = TickerPublisher.from_config()

    def on_payload(payload: TickerPayload):
        cache.store(payload)
        publisher.publish(payload)
        DeltaTickerClient.print_payload(payload)

    symbols = registry.get() or []
    print(f"Loaded symbols from {SymbolRegistry.KEY}: {symbols}")
    print(f"Publishing to Kafka topic {publisher.topic}")
    client = DeltaTickerClient(symbols, on_payload=on_payload)

    refresher = SymbolRefresher(registry, client.update_symbols, SYMBOL_REFRESH_SECONDS)
    refresher.start()
    try:
        client.run()
    finally:
        refresher.stop()
        undelivered = publisher.flush()
        if undelivered:
            print(f"{undelivered} updates were not delivered to Kafka")


if __name__ == "__main__":
    main()
