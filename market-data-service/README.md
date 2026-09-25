# market-data-service

Streams live option tickers from [Delta Exchange](https://www.delta.exchange/) over a websocket, converts each update into a Kafka-ready `TickerPayload`, and caches the latest payload per symbol in Redis with a 10-second TTL.

The symbols to subscribe to are also kept in Redis, and a FastAPI service manages them over HTTP. The ticker re-reads them every 30 seconds, so you can add or remove symbols without restarting anything.

Everything runs in Docker and is driven by `make`.

```
Delta Exchange (wss, v2/ticker)
        │
        ▼
  delta-ticker ──► stdout (JSON, one line per update)
        │    ▲
  write │    │ read every 30s
        ▼    │
  redis ─────┴── ticker:latest:<symbol>   latest payload, TTL 10s
             └── ticker:symbols           set of symbols, no expiry
                        ▲
                        │ CRUD
  symbols-api (FastAPI, :8000) ◄── curl / browser / other services
```

## Prerequisites

- Docker with Compose v2 (Docker Desktop, Rancher Desktop, etc.)
- `make`

With Rancher Desktop, Docker usually listens on `~/.rd/docker.sock`. The Makefile picks this up on its own, so you don't need to set `DOCKER_HOST`.

## Quick start

```sh
cd market-data-service
make up          # build the image, start redis, the ticker and the symbols API

# what to subscribe to (first run only)
curl -X PUT localhost:8000/symbols -H 'content-type: application/json' \
     -d '{"symbols": ["C-BTC-80000-091026", "P-BTC-80000-091026"]}'

make cache-show  # see what's cached (within ~30s)
make down        # stop everything
```

The symbol list is stored in Redis's data volume, so you only need to set it once. It survives `make down` and restarts.

## Makefile commands

Run `make` with no arguments to list every target.

### Everything

| Command | Description |
|---|---|
| `make build` | Build all images |
| `make up` | Build the image, start Redis (waits until healthy), then the ticker and the symbols API |
| `make down` | Stop and remove all containers. The Redis data volume is kept |
| `make restart` | `down`, then `up` |
| `make status` | Show all containers |
| `make logs` | Follow logs from all containers |
| `make test` | Run the unit tests inside Docker |

```sh
$ make up
 Container market-data-redis Healthy
 Container delta-ticker Healthy
 Container symbols-api Healthy

$ make status
NAME                IMAGE                      COMMAND              STATUS                    PORTS
delta-ticker        market-data/delta-ticker   "delta-ticker"       Up 5 seconds
market-data-redis   redis:8                    "docker-entrypoint…" Up 11 seconds (healthy)   0.0.0.0:6379->6379/tcp
symbols-api         market-data/delta-ticker   "delta-ticker-api"   Up 5 seconds (healthy)    0.0.0.0:8000->8000/tcp

$ make test
#11 0.980 41 passed in 0.63s
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
delta-ticker  | {"symbol":"C-BTC-80000-091026","product_id":153512,"strike_price":80000.0,"time":"2026-09-25T13:51:37.953132+05:30","spot_price":84330.4,"mark_price":5148.09436923,"best_bid":5121.0,"best_ask":5177.0,"delta":0.77664966}
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
ticker:latest:C-BTC-80000-091026  ttl=7s
ticker:latest:P-BTC-80000-091026  ttl=7s

$ make cache-get SYMBOL=C-BTC-80000-091026
{"symbol":"C-BTC-80000-091026","product_id":153512,"strike_price":80000.0, ... }
```

`make cache-show` prints `cache empty` when nothing is cached. That happens when the ticker isn't running, no symbols are configured, or no update has arrived in the last 10 seconds.

### Symbols

These targets write to Redis directly with `redis-cli`. They're handy for quick changes, but they skip the symbol-format check that the [Symbols API](#symbols-api) does.

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

### Symbols API

| Command | Description |
|---|---|
| `make api-start` | Build if needed and start the API (starts Redis too if it isn't running) |
| `make api-stop` | Stop the API |
| `make api-restart` | Rebuild and recreate the API. Use this after changing code |
| `make api-status` | Show the API container |
| `make api-logs` | Follow the API's logs |
| `make api-docs` | Open the interactive docs (Swagger UI) in a browser |

The ticker and the API share one image. `make ticker-restart` rebuilds it but only recreates the ticker, so run `make api-restart` as well (or `make restart`) after changing shared code.

## Symbols API

A FastAPI service for creating, reading, updating and deleting the subscribed option symbols, and for reading the latest cached tickers (see [Tickers](#tickers)). It runs in the `symbols-api` container on `http://localhost:8000`. Interactive docs are at [`/docs`](http://localhost:8000/docs) and the OpenAPI schema is at `/openapi.json`.

Writes go to the `ticker:symbols` Redis set. The ticker applies them on its next refresh, within 30 seconds.

| Method | Path | Body | Success | Errors |
|---|---|---|---|---|
| `GET` | `/symbols` | | `200` all symbols | |
| `GET` | `/symbols/{symbol}` | | `200` | `404` not subscribed |
| `POST` | `/symbols` | `{"symbols": [...]}` | `201` adds to the list, returns the full list | `422` invalid symbol or empty list |
| `PUT` | `/symbols` | `{"symbols": [...]}` | `200` replaces the whole list atomically, returns it | `422` invalid symbol or empty list |
| `PUT` | `/symbols/{symbol}` | | `201` added, `200` already there | `422` invalid symbol |
| `DELETE` | `/symbols/{symbol}` | | `204` removed | `404` not subscribed |
| `DELETE` | `/symbols` | | `204` all removed | |
| `GET` | `/health` | | `200` if Redis is reachable | `503` |

Any endpoint returns `503` with `Redis unavailable: ...` if Redis can't be reached.

**Validation:** only option symbols are accepted, in the form `<C|P>-<underlying>-<strike>-<DDMMYY>`, e.g. `C-BTC-80000-091026`. Anything else, such as the perpetual `BTCUSD`, gets a `422`. Duplicates in a request body are dropped. The API doesn't check that a symbol is actually listed on Delta; a valid-looking but unlisted symbol is accepted, and the ticker just never receives updates for it.

### Examples

```sh
# Replace the whole list
$ curl -X PUT localhost:8000/symbols -H 'content-type: application/json' \
       -d '{"symbols": ["C-BTC-80000-091026", "P-BTC-80000-091026"]}'
{"symbols":["C-BTC-80000-091026","P-BTC-80000-091026"],"count":2,"note":"The ticker applies changes within 30s."}

# Add to the list
$ curl -X POST localhost:8000/symbols -H 'content-type: application/json' \
       -d '{"symbols": ["C-BTC-84000-091026"]}'
{"symbols":["C-BTC-80000-091026","C-BTC-84000-091026","P-BTC-80000-091026"],"count":3,"note":"The ticker applies changes within 30s."}

# List
$ curl localhost:8000/symbols
{"symbols":["C-BTC-80000-091026","C-BTC-84000-091026","P-BTC-80000-091026"],"count":3,"note":"The ticker applies changes within 30s."}

# Check one
$ curl localhost:8000/symbols/C-BTC-84000-091026
{"symbol":"C-BTC-84000-091026"}

# Add one (idempotent: 201 the first time, 200 after)
$ curl -X PUT -w '%{http_code}\n' localhost:8000/symbols/P-BTC-84000-091026
{"symbol":"P-BTC-84000-091026"}201

# Remove one
$ curl -X DELETE -w '%{http_code}\n' localhost:8000/symbols/C-BTC-84000-091026
204

$ curl localhost:8000/symbols/C-BTC-84000-091026
{"detail":"C-BTC-84000-091026 is not subscribed"}

# Remove all
$ curl -X DELETE -w '%{http_code}\n' localhost:8000/symbols
204

# Rejected: not an option symbol
$ curl -X POST -w '\n%{http_code}\n' localhost:8000/symbols -H 'content-type: application/json' \
       -d '{"symbols": ["BTCUSD"]}'
{"detail":[{"type":"string_pattern_mismatch","loc":["body","symbols",0],"msg":"String should match pattern '^[CP]-[A-Z0-9]+-\\d+(\\.\\d+)?-\\d{6}$'","input":"BTCUSD", ...}]}
422
```

Once a change is applied, the ticker logs it:

```
delta-ticker  | Symbols changed: +['C-BTC-80000-091026', 'P-BTC-80000-091026'] -['C-BTC-79500-250926', 'P-BTC-79500-250926']
```

### Tickers

Read-only access to the latest payload per symbol, straight from the Redis cache.

| Method | Path | Success | Errors |
|---|---|---|---|
| `GET` | `/tickers` | `200` every cached ticker, sorted by symbol, with a `count` | `503` Redis unavailable |
| `GET` | `/tickers/{symbol}` | `200` the latest payload for that symbol | `404` no update in the last 10s, `503` Redis unavailable |

Each ticker has the fields listed in [Payload fields](#payload-fields). A symbol only appears if it updated within the cache TTL (10 seconds). A subscribed symbol that has stopped trading, or one added less than about 30 seconds ago, won't be listed yet. `/tickers/` with a trailing slash redirects to `/tickers`, so use `curl -L` if you include the slash.

```sh
$ curl localhost:8000/tickers
{"tickers":[{"symbol":"C-BTC-80000-091026","product_id":153512,"strike_price":80000.0,"time":"2026-09-25T13:51:37.953132+05:30","spot_price":84330.4,"mark_price":5148.09436923,"best_bid":5121.0,"best_ask":5177.0,"delta":0.77664966},
            {"symbol":"P-BTC-80000-091026", ... }],
 "count":2}

$ curl localhost:8000/tickers/C-BTC-80000-091026
{"symbol":"C-BTC-80000-091026","product_id":153512,"strike_price":80000.0,"time":"2026-09-25T13:51:37.953132+05:30","spot_price":84330.4,"mark_price":5148.09436923,"best_bid":5121.0,"best_ask":5177.0,"delta":0.77664966}

$ curl -w ' (%{http_code})\n' localhost:8000/tickers/C-BTC-84000-091026
{"detail":"No ticker for C-BTC-84000-091026 in the last 10s"} (404)
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
delta-ticker  | {"symbol":"P-BTC-80000-091026", ... }
delta-ticker  | {"symbol":"C-BTC-80000-091026", ... }
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
make cache-get SYMBOL=C-BTC-80000-091026
# or, through the API
curl localhost:8000/tickers/C-BTC-80000-091026
```

Compare the payload's `time` field with the current time in IST (`TZ=Asia/Kolkata date`). It should be only a few seconds old.

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
| Accepted symbol format | `OPTION_SYMBOL_PATTERN` in [`src/delta_ticker/config.py`](src/delta_ticker/config.py) | `<C\|P>-<underlying>-<strike>-<DDMMYY>` |
| Symbols API host port | `API_PORT` environment variable (used by docker-compose and the Makefile) | `8000` |

Changing a value in `config.py` needs a rebuild: `make restart`, or `make ticker-restart` and `make api-restart`.
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
| `symbol`, `product_id`, `strike_price` | Instrument details |
| `time` | Exchange timestamp in Indian Standard Time, ISO 8601 with a `+05:30` offset and microsecond precision, e.g. `2026-09-25T13:51:37.953132+05:30`. Converted from Delta's microseconds-since-epoch `timestamp` |
| `spot_price`, `mark_price` | Underlying spot and the option's mark price |
| `best_bid`, `best_ask` | Top-of-book prices |
| `delta` | Option delta (`null` for non-options) |

```json
{"symbol":"C-BTC-80000-091026","product_id":153512,"strike_price":80000.0,"time":"2026-09-25T13:51:37.953132+05:30","spot_price":84330.4,"mark_price":5148.09436923,"best_bid":5121.0,"best_ask":5177.0,"delta":0.77664966}
```

Numbers are floats. Anything Delta doesn't send is `null`.

For Kafka, `TickerPayload.key()` returns the symbol as the message key, which keeps each instrument's updates in order within a partition. `TickerPayload.to_json()` returns the message value.

## Project layout

```
market-data-service/
├── Makefile
├── docker-compose.yml       # redis, delta-ticker and symbols-api services
├── Dockerfile               # stages: base, test, runtime
├── pyproject.toml           # package metadata, dependencies, `delta-ticker` and `delta-ticker-api` commands
├── src/delta_ticker/
│   ├── __main__.py          # ticker entry point: wiring
│   ├── api.py               # FastAPI: /symbols CRUD, /tickers read-only, entry point
│   ├── config.py            # constants: URLs, Redis keys, TTL, refresh interval, symbol format, API port
│   ├── client.py            # DeltaTickerClient: websocket subscribe/unsubscribe + parse
│   ├── payload.py           # TickerPayload: Kafka-ready record
│   ├── cache.py             # TickerCache: latest payload per symbol in Redis
│   └── symbols.py           # SymbolRegistry (symbol set CRUD) + SymbolRefresher (re-read every 30s)
├── tests/
│   ├── test_payload.py
│   ├── test_cache.py
│   ├── test_symbols.py
│   └── test_api.py
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
delta-ticker-api                         # in another terminal; serves on :8000 (stop the symbols-api container first)
```
