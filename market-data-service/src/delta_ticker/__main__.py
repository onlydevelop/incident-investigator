import os

from delta_ticker.cache import DEFAULT_REDIS_URL, TickerCache
from delta_ticker.client import DeltaTickerClient
from delta_ticker.payload import TickerPayload
from delta_ticker.symbols import SymbolRefresher, SymbolRegistry

CACHE_TTL_SECONDS = 10
SYMBOL_REFRESH_SECONDS = 30


def main():
    redis_url = os.environ.get("REDIS_URL", DEFAULT_REDIS_URL)
    cache = TickerCache.from_url(redis_url, CACHE_TTL_SECONDS)
    registry = SymbolRegistry.from_url(redis_url)

    def on_payload(payload: TickerPayload):
        cache.store(payload)
        DeltaTickerClient.print_payload(payload)

    symbols = registry.get() or []
    print(f"Loaded symbols from {SymbolRegistry.KEY}: {symbols}")
    client = DeltaTickerClient(symbols, on_payload=on_payload)

    refresher = SymbolRefresher(registry, client.update_symbols, SYMBOL_REFRESH_SECONDS)
    refresher.start()
    try:
        client.run()
    finally:
        refresher.stop()


if __name__ == "__main__":
    main()
