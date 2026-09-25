import os

from delta_ticker.cache import DEFAULT_REDIS_URL, TickerCache
from delta_ticker.client import DeltaTickerClient
from delta_ticker.payload import TickerPayload

# Replace with your list of option symbols, or use an option-chain shorthand like "BTC-150426"
OPTION_SYMBOLS = [
    "C-BTC-79500-250926",
    "P-BTC-79500-250926",
]

CACHE_TTL_SECONDS = 10


def main():
    cache = TickerCache.from_url(os.environ.get("REDIS_URL", DEFAULT_REDIS_URL), CACHE_TTL_SECONDS)

    def on_payload(payload: TickerPayload):
        cache.store(payload)
        DeltaTickerClient.print_payload(payload)

    DeltaTickerClient(OPTION_SYMBOLS, on_payload=on_payload).run()


if __name__ == "__main__":
    main()
