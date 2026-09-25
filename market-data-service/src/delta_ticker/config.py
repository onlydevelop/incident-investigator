import os

# Public websocket endpoint (no authentication required for ticker channel)
WEBSOCKET_URL = "wss://socket.india.delta.exchange"

# Set to redis://redis:6379/0 by docker-compose.
REDIS_URL = os.environ.get("REDIS_URL", "redis://localhost:6379/0")

# Latest payload per symbol: <CACHE_KEY_PREFIX><symbol>, expiring after CACHE_TTL_SECONDS.
CACHE_KEY_PREFIX = "ticker:latest:"
CACHE_TTL_SECONDS = 10

# Redis set of symbols to subscribe to (no expiry), re-read every SYMBOL_REFRESH_SECONDS.
# The Makefile's CACHE_PREFIX and SYMBOLS_KEY must match these keys.
SYMBOLS_KEY = "ticker:symbols"
SYMBOL_REFRESH_SECONDS = 30

# Option symbols accepted by the symbols API: <C|P>-<underlying>-<strike>-<DDMMYY>, e.g. C-BTC-80000-091026.
OPTION_SYMBOL_PATTERN = r"^[CP]-[A-Z0-9]+-\d+(\.\d+)?-\d{6}$"

# Symbols API (uvicorn).
API_HOST = os.environ.get("API_HOST", "0.0.0.0")
API_PORT = int(os.environ.get("API_PORT", "8000"))
