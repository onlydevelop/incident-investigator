from typing import Optional

import httpx

from order_service.config import MARKET_DATA_TIMEOUT_SECONDS, MARKET_DATA_URL


class MarketDataError(Exception):
    """market-data-service couldn't be reached, or refused the request."""


class SymbolsClient:
    """Client for market-data-service's symbols API (`PUT /symbols/{symbol}` subscribes one symbol)."""

    def __init__(self, base_url: str = MARKET_DATA_URL, client: Optional[httpx.Client] = None):
        self.client = client or httpx.Client(base_url=base_url, timeout=MARKET_DATA_TIMEOUT_SECONDS)

    def subscribe(self, symbol: str) -> bool:
        """Makes sure the ticker streams `symbol`; returns True if it was newly added.
        Idempotent: subscribing a symbol that's already there is a no-op."""
        try:
            response = self.client.put(f"/symbols/{symbol}")
        except httpx.HTTPError as e:
            raise MarketDataError(f"market-data-service unreachable: {e!r}") from e
        if response.status_code not in (200, 201):
            raise MarketDataError(f"market-data-service returned {response.status_code}: {response.text[:200]}")
        return response.status_code == 201
