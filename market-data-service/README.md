# market-data-service

Streams live option tickers from [Delta Exchange](https://www.delta.exchange/) over a websocket, converts each update into a Kafka-ready `TickerPayload`, and caches the latest payload per symbol in Redis with a 10-second TTL.

Everything runs in Docker and is driven by `make`.

```
Delta Exchange (wss, v2/ticker)
        │
        ▼
  delta-ticker container ──► stdout (JSON, one line per update)
        │
        ▼
  redis container   key: ticker:latest:<symbol>   TTL: 10s
```

## Prerequisites

- Docker with Compose v2 (Docker Desktop, Rancher Desktop, etc.)
- `make`

With Rancher Desktop, Docker usually listens on `~/.rd/docker.sock`. The Makefile picks this up on its own, so you don't need to set `DOCKER_HOST`.

## Quick start

```sh
cd market-data-service
make up            # build the ticker, start redis, start streaming
make cache-show    # see what's cached
make down          # stop everything
```

## Makefile commands

Run `make` with no arguments to list every target.

### Everything

| Command | Description |
|---|---|
| `make build` | Build all images |
| `make up` | Build the ticker, start Redis (waits until healthy), start the ticker |
| `make down` | Stop and remove all containers. The Redis data volume is kept |
| `make restart` | `down`, then `up` |
| `make status` | Show all containers |
| `make logs` | Follow logs from all containers |
| `make test` | Run the unit tests inside Docker |

```sh
$ make up
 Container market-data-redis Healthy
 Container delta-ticker Started

$ make status
NAME                IMAGE                      STATUS                    PORTS
delta-ticker        market-data/delta-ticker   Up 7 seconds
market-data-redis   redis:8                    Up 12 seconds (healthy)   0.0.0.0:6379->6379/tcp

$ make test
#11 0.921 6 passed in 0.17s
```

`make test` builds the `test` stage of the Dockerfile, so a failing test fails the command.

### Ticker

| Command | Description |
|---|---|
| `make ticker-build` | Build the ticker image |
| `make ticker-start` | Build if needed and start the ticker in the background (starts Redis too if it isn't running) |
| `make ticker-stop` | Stop the ticker container |
| `make ticker-restart` | Rebuild and recreate the ticker. Use this after changing code |
| `make ticker-status` | Show the ticker container |
| `make ticker-logs` | Follow the ticker's output |
| `make ticker-run` | Run the ticker in the foreground inside Docker. Ctrl+C to stop |

```sh
$ make ticker-logs
delta-ticker  | Socket opened
delta-ticker  | {"source":"delta.exchange","symbol":"C-BTC-79500-250926","product_id":151279,"contract_type":"call_options","underlying":"BTC","strike_price":79500.0,"timestamp_us":1790318731292250,"spot_price":84033.8,"mark_price":4534.7931089,"best_bid":4494.0,"bid_size":4051.0,"best_ask":4542.0,"ask_size":2695.0,"bid_iv":5e-06,"ask_iv":1.18664652,"mark_iv":0.79622592,"delta":0.99628648,"gamma":6.37e-06,"rho":0.47387059,"theta":-44.16395986,"vega":0.22779773,"open_interest":0.785,"volume":0.601}
```

### Redis (dependencies)

| Command | Description |
|---|---|
| `make deps-start` | Start Redis and wait until it's healthy |
| `make deps-stop` | Stop Redis. Data is kept |
| `make deps-restart` | Restart Redis |
| `make deps-status` | Show the Redis container |
| `make deps-logs` | Follow Redis logs |

### Cache

| Command | Description |
|---|---|
| `make cache-show` | List cached symbols and their remaining TTL |
| `make cache-get SYMBOL=<symbol>` | Print the cached payload for one symbol |

```sh
$ make cache-show
ticker:latest:C-BTC-79500-250926  ttl=7s
ticker:latest:P-BTC-79500-250926  ttl=7s

$ make cache-get SYMBOL=C-BTC-79500-250926
{"source":"delta.exchange","symbol":"C-BTC-79500-250926","product_id":151279, ... }
```

`make cache-show` prints `cache empty` when nothing is cached. That happens when the ticker isn't running, or no update has arrived in the last 10 seconds.

Redis is also published on `localhost:6379`, so any Redis client can read the cache:

```sh
redis-cli get ticker:latest:C-BTC-79500-250926
```

## Configuration

| What | Where | Default |
|---|---|---|
| Symbols to subscribe to | `OPTION_SYMBOLS` in [`src/delta_ticker/__main__.py`](src/delta_ticker/__main__.py) | `C-BTC-79500-250926`, `P-BTC-79500-250926` |
| Cache TTL | `CACHE_TTL_SECONDS` in [`src/delta_ticker/__main__.py`](src/delta_ticker/__main__.py) | `10` |
| Redis connection | `REDIS_URL` environment variable | `redis://redis:6379/0` in Docker, `redis://localhost:6379/0` otherwise |
| Redis host port | `REDIS_PORT` environment variable | `6379` |

Option symbols follow `<C|P>-<underlying>-<strike>-<DDMMYY>`, e.g. `C-BTC-79500-250926` is a BTC call, strike 79,500, expiring 25 Sep 2026. Expired symbols stop updating, so change the list as options expire, then run `make ticker-restart`.

## Cache format

- **Key:** `ticker:latest:<symbol>`
- **Value:** the payload as compact JSON, the same as `TickerPayload.to_json()`
- **TTL:** every update overwrites the key and resets the TTL to 10 seconds. A symbol that stops updating drops out of the cache 10 seconds after its last update.

If Redis is unreachable, the ticker logs `Failed to cache <symbol>: ...` and keeps streaming.

### Payload fields

| Field | Description |
|---|---|
| `source` | Always `delta.exchange` |
| `symbol`, `product_id`, `contract_type`, `underlying`, `strike_price` | Instrument details |
| `timestamp_us` | Exchange timestamp, microseconds since the epoch |
| `spot_price`, `mark_price` | Underlying spot and the option's mark price |
| `best_bid`, `bid_size`, `best_ask`, `ask_size` | Top of book |
| `bid_iv`, `ask_iv`, `mark_iv` | Implied volatilities |
| `delta`, `gamma`, `rho`, `theta`, `vega` | Greeks (`null` for non-options) |
| `open_interest`, `volume` | Open interest and 24h volume |

Numbers are floats. Anything Delta doesn't send is `null`.

For Kafka, `TickerPayload.key()` returns the symbol as the message key, which keeps each instrument's updates in order within a partition. `TickerPayload.to_json()` returns the message value.

## Project layout

```
market-data-service/
├── Makefile
├── docker-compose.yml       # redis + delta-ticker services
├── Dockerfile               # stages: base, test, runtime
├── pyproject.toml           # package metadata, dependencies, `delta-ticker` command
├── src/delta_ticker/
│   ├── __main__.py          # entry point: symbols, TTL, wiring
│   ├── client.py            # DeltaTickerClient: websocket subscribe + parse
│   ├── payload.py           # TickerPayload: Kafka-ready record
│   └── cache.py             # TickerCache: latest payload per symbol in Redis
├── tests/
│   ├── test_payload.py
│   └── test_cache.py
└── experiment.ipynb         # original exploration notebook (standalone)
```

## Local development without Docker

Optional. Requires Python 3.11+.

```sh
python -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
pytest
make deps-start                          # Redis still runs in Docker
delta-ticker                             # connects to redis://localhost:6379/0
```
