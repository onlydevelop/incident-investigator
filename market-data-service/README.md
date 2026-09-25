# market-data-service

Streams live option tickers from [Delta Exchange](https://www.delta.exchange/) over a websocket, converts each update into a Kafka-ready `TickerPayload`, and caches the latest payload per symbol in Redis with a 10-second TTL.

The symbols to subscribe to are also kept in Redis. The ticker re-reads them every 30 seconds, so you can add or remove symbols without restarting anything.

Everything runs in Docker and is driven by `make`.

```
Delta Exchange (wss, v2/ticker)
        │                        ┌──── every 30s: read ticker:symbols (set, no expiry)
        ▼                        │
  delta-ticker container ────────┤
        │                        └──► stdout (JSON, one line per update)
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
make up                                                        # build the ticker, start redis and the ticker
make symbols-set SYMBOLS="C-BTC-80000-091026 P-BTC-80000-091026"  # what to subscribe to (first run only)
make cache-show                                                # see what's cached (within ~30s)
make down                                                      # stop everything
```

The symbol list is stored in Redis's data volume, so you only need to set it once. It survives `make down` and restarts.

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
#11 0.980 15 passed in 0.18s
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

`make cache-show` prints `cache empty` when nothing is cached. That happens when the ticker isn't running, no symbols are configured, or no update has arrived in the last 10 seconds.

### Symbols

| Command | Description |
|---|---|
| `make symbols-show` | List the symbols the ticker subscribes to |
| `make symbols-add SYMBOLS="<symbol> ..."` | Add one or more symbols |
| `make symbols-remove SYMBOLS="<symbol> ..."` | Remove one or more symbols |
| `make symbols-set SYMBOLS="<symbol> ..."` | Replace the whole list in one step |
| `make symbols-clear` | Remove all symbols |

```sh
$ make symbols-add SYMBOLS="C-BTC-80000-091026 P-BTC-80000-091026"
C-BTC-79500-250926
C-BTC-80000-091026
P-BTC-79500-250926
P-BTC-80000-091026

$ make ticker-logs        # within 30s
delta-ticker  | Symbols changed: +['C-BTC-80000-091026', 'P-BTC-80000-091026'] -[]

$ make symbols-remove SYMBOLS="C-BTC-79500-250926 P-BTC-79500-250926"
C-BTC-80000-091026
P-BTC-80000-091026

$ make ticker-logs        # within 30s
delta-ticker  | Symbols changed: +[] -['C-BTC-79500-250926', 'P-BTC-79500-250926']
```

Changes take effect on the ticker's next refresh, up to 30 seconds later. Only the difference is sent to Delta: new symbols are subscribed and removed ones are unsubscribed, on the same connection. A removed symbol's cache entry expires 10 seconds after its last update.

Redis is also published on `localhost:6379`, so any Redis client can read the cache or change the symbols:

```sh
redis-cli get ticker:latest:C-BTC-79500-250926
redis-cli sadd ticker:symbols C-BTC-84000-091026 P-BTC-84000-091026
```

## Checking that it works

### 1. Is data being fetched?

```sh
make ticker-logs
```

A healthy ticker logs the symbols it loaded, `Socket opened`, then a subscriptions message listing your symbols, then one JSON line per update:

```
delta-ticker  | Loaded symbols from ticker:symbols: ['C-BTC-79500-250926', 'P-BTC-79500-250926']
delta-ticker  | Socket opened
delta-ticker  | {
delta-ticker  |   "channels": [
delta-ticker  |     {
delta-ticker  |       "name": "v2/ticker",
delta-ticker  |       "symbols": ["C-BTC-79500-250926", "P-BTC-79500-250926"]
delta-ticker  |     }
delta-ticker  |   ],
delta-ticker  |   "type": "subscriptions"
delta-ticker  | }
delta-ticker  | {"source":"delta.exchange","symbol":"P-BTC-79500-250926", ... }
delta-ticker  | {"source":"delta.exchange","symbol":"C-BTC-79500-250926", ... }
```

- **`No symbols configured yet; waiting for the next refresh`:** the `ticker:symbols` set is empty. Add symbols with `make symbols-add`.
- **Subscriptions message but no JSON lines:** the symbols aren't trading, most likely because they've expired. See [Configuration](#configuration).
- **An `"error"` in the subscriptions message:** Delta rejected the subscription, for example because of a wrong channel name or an unknown symbol.

### 2. Is the cache being filled?

```sh
make cache-show
```

```
ticker:latest:C-BTC-79500-250926  ttl=5s
ticker:latest:P-BTC-79500-250926  ttl=10s
```

Run it a few times. Each symbol's TTL should keep jumping back towards 10s, because every update resets it. If a TTL keeps counting down and the key then disappears, updates for that symbol have stopped.

### 3. Is the cached data fresh?

```sh
make cache-get SYMBOL=C-BTC-79500-250926
```

Compare the payload's `timestamp_us` field (microseconds since the epoch) with the current time. It should be only a few seconds old.

### 4. Watch the Redis writes live

```sh
docker compose exec redis redis-cli monitor
```

Each `SET "ticker:latest:..." ... "EX" "10"` line is the ticker writing to the cache. Press Ctrl+C to stop.

With Rancher Desktop, plain `docker` commands need `DOCKER_HOST` set first. The Makefile does this for you, but a raw `docker compose` command doesn't:

```sh
export DOCKER_HOST=unix://$HOME/.rd/docker.sock
```

### Troubleshooting

| Symptom | Likely cause | What to do |
|---|---|---|
| `cache empty`, ticker logs are streaming | The ticker can't reach Redis. Look for `Failed to cache ...` in the logs | `make deps-status`, then `make deps-restart` |
| `cache empty`, no ticker logs | The ticker isn't running | `make status`, then `make ticker-restart` |
| `cache empty`, `No symbols configured yet` in the logs | The `ticker:symbols` set is empty | `make symbols-add SYMBOLS="..."`, then wait up to 30s |
| A symbol you added isn't in the cache | The next refresh hasn't happened yet, or the symbol isn't trading | Wait 30s, then check `make ticker-logs` for `Symbols changed` |
| Subscriptions message, but no updates | The symbols have expired or aren't trading | Swap them with `make symbols-remove` / `make symbols-add` |
| `failed to connect to the docker API` | Docker isn't running, or `DOCKER_HOST` isn't set for a raw `docker` command | Start Docker Desktop or Rancher Desktop. Use the `make` targets, or export `DOCKER_HOST` as shown above |

## Configuration

| What | Where | Default |
|---|---|---|
| Symbols to subscribe to | Redis set `ticker:symbols` (no expiry). Manage with the `make symbols-*` targets | empty |
| Symbol refresh interval | `SYMBOL_REFRESH_SECONDS` in [`src/delta_ticker/config.py`](src/delta_ticker/config.py) | `30` |
| Cache TTL | `CACHE_TTL_SECONDS` in [`src/delta_ticker/config.py`](src/delta_ticker/config.py) | `10` |
| Redis key names | `CACHE_KEY_PREFIX`, `SYMBOLS_KEY` in [`src/delta_ticker/config.py`](src/delta_ticker/config.py). Keep the Makefile's `CACHE_PREFIX` / `SYMBOLS_KEY` in sync | `ticker:latest:`, `ticker:symbols` |
| Delta websocket URL | `WEBSOCKET_URL` in [`src/delta_ticker/config.py`](src/delta_ticker/config.py) | `wss://socket.india.delta.exchange` |
| Redis connection | `REDIS_URL` environment variable, read in [`src/delta_ticker/config.py`](src/delta_ticker/config.py) | `redis://redis:6379/0` in Docker, `redis://localhost:6379/0` otherwise |

Changing a value in `config.py` needs a rebuild: `make ticker-restart`.
| Redis host port | `REDIS_PORT` environment variable | `6379` |

Option symbols follow `<C|P>-<underlying>-<strike>-<DDMMYY>`, e.g. `C-BTC-79500-250926` is a BTC call, strike 79,500, expiring 25 Sep 2026. Expired symbols stop updating, so swap them out as options expire, e.g. `make symbols-remove SYMBOLS=...` and `make symbols-add SYMBOLS=...`. No restart is needed.

If Redis can't be read during a refresh, the ticker logs `Failed to read symbols from ticker:symbols: ...` and keeps its current subscriptions.

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
│   ├── __main__.py          # entry point: wiring
│   ├── config.py            # constants: URLs, Redis keys, TTL, refresh interval
│   ├── client.py            # DeltaTickerClient: websocket subscribe/unsubscribe + parse
│   ├── payload.py           # TickerPayload: Kafka-ready record
│   ├── cache.py             # TickerCache: latest payload per symbol in Redis
│   └── symbols.py           # SymbolRegistry + SymbolRefresher: symbol list from Redis, every 30s
├── tests/
│   ├── test_payload.py
│   ├── test_cache.py
│   └── test_symbols.py
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
