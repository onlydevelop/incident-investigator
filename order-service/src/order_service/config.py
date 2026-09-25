import os

# Postgres in the shared infra stack. Set to postgres:5432 by docker-compose.
DATABASE_URL = os.environ.get("DATABASE_URL", "postgresql://portfolio:portfolio@localhost:5432/portfolio")
# How long a request waits for a free connection (or for Postgres to come back) before failing.
DATABASE_TIMEOUT_SECONDS = 5
# Session time zone for timestamps read back from Postgres (stored as timestamptz, so this is display
# only). IST matches the `time` field in market-data-service's payloads.
DATABASE_TIMEZONE = "Asia/Kolkata"

# market-data-service's symbols API; creating a position subscribes its symbol there.
# Set to http://symbols-api:8000 by docker-compose.
MARKET_DATA_URL = os.environ.get("MARKET_DATA_URL", "http://localhost:8000")
MARKET_DATA_TIMEOUT_SECONDS = 5

# Ticker feed published by market-data-service. Set to kafka:9092 by docker-compose.
KAFKA_BOOTSTRAP_SERVERS = os.environ.get("KAFKA_BOOTSTRAP_SERVERS", "localhost:9094")
TICKER_TOPIC = "market-data.ticker"
CONSUMER_GROUP = "order-service.position-updater"

# Same format market-data-service accepts: <C|P>-<underlying>-<strike>-<DDMMYY>, e.g. C-BTC-80000-091026.
OPTION_SYMBOL_PATTERN = r"^[CP]-[A-Z0-9]+-\d+(\.\d+)?-\d{6}$"

# Positions API (uvicorn).
API_HOST = os.environ.get("API_HOST", "0.0.0.0")
API_PORT = int(os.environ.get("API_PORT", "8001"))
