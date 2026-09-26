# order-service

[![CI](../../../actions/workflows/ci.yml/badge.svg?branch=main)](../../../actions/workflows/ci.yml?query=branch%3Amain)
[![tests](../../../raw/badges/tests-order-service.svg)](#tests)
[![coverage](../../../raw/badges/coverage-order-service.svg)](#tests)

Paper-trading positions for Delta Exchange options. There are no real orders: a position is recorded in Postgres and priced from the live ticker feed that [market-data-service](../market-data-service/README.md) publishes to Kafka.

Creating a position takes two steps, and it's all or nothing:

1. **Store it.** The position is inserted into the `positions` table with status `pending` and no prices yet.
2. **Subscribe its symbol.** order-service calls market-data-service's `PUT /symbols/{symbol}` so the ticker streams that symbol. If this fails, the insert is rolled back and the API returns `502`.

Pricing then happens in `position-updater`, a Kafka consumer:

- **The first tick after the position was created sets `entry_price`,** and the status becomes `open`. A **buy enters at the best ask** and a **sell enters at the best bid**, the prices you'd actually trade at.
- **Every tick after that sets `current_price`** to the price the position would close at now: the **bid for a buy** and the **ask for a sell**.

```
  curl ──► orders-api (FastAPI, :8001)
               │  1. INSERT pending          2. PUT /symbols/{symbol}
               ▼                                     │
           postgres ◄────────┐                       ▼
           positions         │              market-data-service ──► kafka: market-data.ticker
                             │                                               │
                             │  first tick after `time`: entry_price, open   │
                             │  later ticks: current_price                   │
                             └──────────────── position-updater ◄────────────┘
```

Everything runs in Docker and is driven by `make`. Postgres and Kafka come from the shared infra stack in [`../infra`](../infra/README.md).

## Quick start

```sh
cd order-service
make up          # starts infra and market-data-service if needed, then orders-api and position-updater

curl -X POST localhost:8001/positions -H 'content-type: application/json' \
     -d '{"symbol": "C-BTC-80000-091026", "side": "buy", "qty": 1}'

make positions-show   # entry_price appears with the next tick (up to ~30s for a newly subscribed symbol)
make down             # stops orders-api and position-updater only
```

## Positions API

The API runs in the `orders-api` container on `http://localhost:8001`. Interactive docs are at [`/docs`](http://localhost:8001/docs).

| Method | Path | Body | Success | Errors |
|---|---|---|---|---|
| `POST` | `/positions` | `{"symbol", "side", "qty"}` | `201` the new position, `pending` | `422` invalid body, `502` market-data-service couldn't subscribe the symbol (nothing is stored) |
| `GET` | `/positions` | | `200` all positions, oldest first, with a `count`. Filter with `?symbol=` and/or `?status=pending\|open` | `422` invalid filter |
| `GET` | `/positions/{id}` | | `200` | `404` |
| `DELETE` | `/positions/{id}` | | `204` | `404` |
| `GET` | `/health` | | `200` if Postgres is reachable | `503` |

Any endpoint returns `503` with `Postgres unavailable: ...` if Postgres can't be reached.

**Request fields**

| Field | Rules |
|---|---|
| `symbol` | An option symbol, `<C\|P>-<underlying>-<strike>-<DDMMYY>`, e.g. `C-BTC-80000-091026`. This is the same check market-data-service applies |
| `side` | `buy` or `sell` |
| `qty` | Greater than 0. Decimals are allowed |

Unknown fields are rejected with a `422`, both in the body (e.g. `quantity` or `entry_price`) and in the `GET /positions` query string (e.g. `?side=buy`). That way a typo fails loudly instead of being silently ignored.

**Position fields**

| Field | Description |
|---|---|
| `id` | Assigned by Postgres |
| `time` | When the position was created |
| `symbol`, `side`, `qty` | As requested |
| `status` | `pending` until the entry tick, then `open` |
| `entry_price`, `entry_time` | Ask (buy) or bid (sell) from the first tick after `time`, and that tick's exchange time. `null` while pending |
| `current_price`, `current_price_time` | Bid (buy) or ask (sell) from the latest tick after the entry, and that tick's exchange time. `null` until the tick after the entry |

Times are ISO 8601 in IST (`+05:30`), the same as market-data-service. Prices and `qty` are JSON numbers.

### Examples

```sh
$ curl -X POST localhost:8001/positions -H 'content-type: application/json' \
       -d '{"symbol": "C-BTC-80000-091026", "side": "buy", "qty": 1}'
{"id":1,"time":"2026-09-25T14:42:43.815981+05:30","symbol":"C-BTC-80000-091026","side":"buy","qty":1.0,"status":"pending",
 "entry_price":null,"entry_time":null,"current_price":null,"current_price_time":null}

# a few seconds later
$ curl localhost:8001/positions/1
{"id":1,"time":"2026-09-25T14:42:43.815981+05:30","symbol":"C-BTC-80000-091026","side":"buy","qty":1.0,"status":"open",
 "entry_price":5411.0,"entry_time":"2026-09-25T14:42:48.261506+05:30",
 "current_price":5344.0,"current_price_time":"2026-09-25T14:44:32.901703+05:30"}

$ curl 'localhost:8001/positions?status=open'
{"positions":[...],"count":2}

$ curl -X DELETE -w '%{http_code}\n' localhost:8001/positions/1
204

# market-data-service down: nothing is stored
$ curl -X POST localhost:8001/positions -H 'content-type: application/json' \
       -d '{"symbol": "C-BTC-80000-091026", "side": "buy", "qty": 1}'
{"detail":"Couldn't subscribe C-BTC-80000-091026 in market-data-service, so no position was created: market-data-service unreachable: ..."}
```

## How ticks are applied

`position-updater` consumes `market-data.ticker` as the consumer group `order-service.position-updater`. Each message is one [`TickerPayload`](../market-data-service/README.md#payload-fields). The updater reads its `symbol`, `time`, `best_bid` and `best_ask`, and runs two `UPDATE`s in one transaction on that symbol's positions ([`store.py`](src/order_service/store.py)):

1. **Open positions:** set `current_price` from the closing side. The tick must be later than both `entry_time` and the previous `current_price_time`.
2. **Pending positions:** set `entry_price` from the trading side and mark them `open`. The tick must be no earlier than the position's `time`.

Step 1 runs first, so the tick that opens a position doesn't also set its `current_price`.

Every check uses the tick's exchange time, which makes applying ticks safe to repeat:

- **A tick from before the position was created never sets its entry.** That can happen with a tick that was already in flight, or with a consumer that is catching up after a restart.
- **A replayed or out-of-order tick never rolls a price back.**
- **A missing quote only affects the side that needs it.** A tick with no ask leaves pending buys waiting and still opens pending sells.

A new consumer group starts from live prices (`auto.offset.reset=latest`). After a restart it resumes from its last committed offset. If Postgres is unavailable, the updater logs `Failed to apply tick ...` and carries on; the next tick for that symbol catches up.

**Timing:** a symbol that market-data-service already streams opens on its next tick, typically within a few seconds. A newly subscribed symbol has to wait for market-data-service's next symbol refresh, up to 30 seconds.

## Makefile commands

Run `make` with no arguments to list every target.

| Command | Description |
|---|---|
| `make up` | Start the shared infra and market-data-service if they aren't running, then build and start `orders-api` and `position-updater` |
| `make down` | Stop and remove `orders-api` and `position-updater`. Infra and market-data-service keep running |
| `make restart` | `down`, then `up` |
| `make status` / `make logs` | Show / follow both containers |
| `make test` | Run the tests in Docker against the shared Postgres (see [Tests](#tests)) |
| `make coverage` | Run the tests with a coverage report in the terminal and in `htmlcov/`. Fails below 95% |
| `make api-start` / `api-stop` / `api-restart` / `api-status` / `api-logs` / `api-docs` | Manage the API. `api-restart` rebuilds, so use it after changing code |
| `make updater-start` / `updater-stop` / `updater-restart` / `updater-status` / `updater-logs` | Manage the Kafka consumer |
| `make positions-show` | Print the `positions` table |
| `make db-shell` | Open `psql` on the shared database |
| `make deps-start` / `deps-status` | Start / show the shared infra |
| `make market-data-start` | Run market-data-service's `make up` |

The API and the updater share one image. After changing shared code, run `make restart`, or both `api-restart` and `updater-restart`.

```sh
$ make updater-logs
position-updater  | {..., "event": "startup", "message": "Consuming market-data.ticker as order-service.position-updater", ...}
position-updater  | {..., "event": "positions_opened", "message": "Opened 1 position(s) in P-BTC-80000-091026 at bid=596.0 ask=608.0", "symbol": "P-BTC-80000-091026", "opened": 1, ...}
position-updater  | {..., "event": "positions_opened", "message": "Opened 1 position(s) in C-BTC-84000-091026 at bid=2583.0 ask=2601.0", ...}

$ make positions-show
 id |               time               |       symbol       | side | qty | status | entry_price | current_price |        current_price_time
----+----------------------------------+--------------------+------+-----+--------+-------------+---------------+----------------------------------
  1 | 2026-09-25 14:42:43.815981+05:30 | C-BTC-80000-091026 | buy  |   1 | open   |      5411.0 |        5344.0 | 2026-09-25 14:44:32.901703+05:30
  2 | 2026-09-25 14:42:43.840403+05:30 | P-BTC-80000-091026 | sell |   2 | open   |       596.0 |         609.0 | 2026-09-25 14:44:32.901703+05:30
```

## Troubleshooting

| Symptom | Likely cause | What to do |
|---|---|---|
| Position stays `pending` | No ticks for the symbol yet. It may have just been subscribed, it may not be trading or may have expired, or a buy has no ask / a sell has no bid | Wait 30s. Check `curl localhost:8000/tickers/<symbol>` and `make -C ../market-data-service kafka-tail` |
| Position stays `pending`, ticks are flowing | `position-updater` isn't running, or can't reach Kafka | `make updater-status`, `make updater-logs` |
| `POST /positions` returns `502` | market-data-service's API is down | `make market-data-start` |
| `503 Postgres unavailable` | The shared Postgres is down | `make deps-status`, then `make deps-start` |

## Configuration

| What | Where | Default |
|---|---|---|
| Postgres connection | `DATABASE_URL` environment variable, read in [`config.py`](src/order_service/config.py) | `postgres:5432` in Docker, `localhost:5432` otherwise; user, password and database `portfolio` |
| Time zone of returned timestamps | `DATABASE_TIMEZONE` in [`config.py`](src/order_service/config.py) | `Asia/Kolkata` |
| market-data-service symbols API | `MARKET_DATA_URL` environment variable | `http://symbols-api:8000` in Docker, `http://localhost:8000` otherwise |
| Kafka | `KAFKA_BOOTSTRAP_SERVERS` environment variable | `kafka:9092` in Docker, `localhost:9094` otherwise |
| Topic and consumer group | `TICKER_TOPIC`, `CONSUMER_GROUP` in [`config.py`](src/order_service/config.py) | `market-data.ticker`, `order-service.position-updater` |
| Accepted symbol format | `OPTION_SYMBOL_PATTERN` in [`config.py`](src/order_service/config.py). Keep it in sync with market-data-service | `<C\|P>-<underlying>-<strike>-<DDMMYY>` |
| API host port | `ORDERS_API_PORT` environment variable (docker-compose and the Makefile) | `8001` |
| Log level | `LOG_LEVEL` environment variable | `INFO` |
| Trace and metric export | `OTEL_EXPORTER_OTLP_ENDPOINT` and the other standard `OTEL_*` variables. Unset means nothing is exported | unset (set on k3s) |

The schema lives in [`schema.sql`](src/order_service/schema.sql). Both containers apply it on startup with `CREATE ... IF NOT EXISTS`, so it creates the table the first time and doesn't touch it afterwards. There are no migrations yet, so a change to an existing column has to be applied by hand (`make db-shell`).

## Tests

```sh
make test        # 61 tests
make coverage    # the same, plus a coverage report; fails below 95%
```

```
$ make coverage
Name                               Stmts   Miss Branch BrPart  Cover   Missing
------------------------------------------------------------------------------
src/order_service/api.py              59      0      8      0   100%
src/order_service/store.py            55      0      2      0   100%
src/order_service/updater.py          58      0     10      0   100%
...
TOTAL                                266      0     22      0   100%
Required test coverage of 95.0% reached. Total coverage: 100.00%
HTML report: .../order-service/htmlcov/index.html
```

Coverage counts branches as well as lines, and is configured under `[tool.coverage.*]` in [`pyproject.toml`](pyproject.toml). The HTML report is written to `htmlcov/`, which git ignores.

**Postgres:** the tests use the real shared Postgres, not a fake, because the pricing rules are SQL. Each run creates a throwaway schema (`test_<random>`), runs every test against it, and drops it at the end, so the service's own `positions` table is never touched. The `positions` table is emptied between tests, so ids restart at 1. Tests that need Postgres are marked `db` automatically. To run only the rest:

```sh
docker compose run --rm test python -m pytest -m "not db"
```

**Everything else is faked**, in [`tests/fakes.py`](tests/fakes.py):
- market-data-service is `FakeSymbols` in the API tests, and `httpx.MockTransport` in the client tests.
- Kafka is `FakeConsumer`/`FakeMessage`. They drive the real `PositionUpdater.run` loop, including idle polls, partition EOF, broker errors, bad messages and a Postgres outage.
- `tick_bytes()` builds a ticker payload exactly as it arrives from market-data-service.

**Fixtures**, in [`tests/conftest.py`](tests/conftest.py):

| Fixture | Scope | Provides |
|---|---|---|
| `schema` | session | The throwaway schema's name; the schema is dropped at the end |
| `pool` | session | A connection pool pinned to that schema, with the IST session time zone like production |
| `store` | test | A `PositionStore` on an empty table |
| `make_position` | test | Factory: `make_position(side=Side.SELL, qty="2", symbol=PUT)` inserts a pending position |
| `later` | test | `later(seconds)`: a tick time relative to now. Negative values are before the test's positions were created |
| `symbols` | test | A `FakeSymbols` that records subscriptions |
| `client` | test | A FastAPI `TestClient` wired to `store` and `symbols` |
| `create` | test | `create(side="sell", qty=2)`: `POST /positions` through `client` |
| `updater` | test | A `PositionUpdater` on `store` |

## Logs, metrics and traces

[`telemetry.py`](src/order_service/telemetry.py) sets this up for both entry points:
- **Logs:** always one JSON object per line on stdout. Each line has `time`, `level`, `service`, `event` and `message`, the event's own fields (`position_id`, `symbol`, `reason`, ...), and `trace_id` / `span_id` when it was written inside a span. uvicorn's access log is included, minus the `/health` and `/docs` probes.
- **Traces and metrics:** exported over OTLP only when `OTEL_EXPORTER_OTLP_ENDPOINT` is set. The k3s deployment sets it to the collector of the [observability stack](../deploy/observability/README.md). docker-compose and the tests don't set it, so nothing is exported there.

**Traces:**
- **`POST /positions`** covers its Postgres INSERT and the call to market-data-service, which continues the trace on that side.
- **The updater** processes each tick in a `market-data.ticker process` span. It continues the delta-ticker's trace from the message's `traceparent` header, and its Postgres UPDATEs are children.

**Metrics:**

| Process | Metrics |
|---|---|
| orders-api | `http_server_request_duration_seconds`, `positions_created_total{side}` |
| position-updater | `position_updater_ticks_total{result}`, `positions_opened_total`, `position_updater_tick_age_seconds` |
| Both | `db_pool_size`, `db_pool_in_use`, `db_pool_max`, `db_pool_requests_waiting` |

[`deploy/k8s/README.md`](../deploy/k8s/README.md#observability) describes each one.

## Project layout

```
order-service/
├── Makefile
├── docker-compose.yml       # orders-api, position-updater, and a `test` profile; on the shared `portfolio` network
├── Dockerfile               # stages: base, test, runtime
├── pyproject.toml           # `order-service-api` and `order-service-updater` commands
├── src/order_service/
│   ├── api.py               # FastAPI: /positions create/list/get/delete, /health (sync handlers)
│   ├── schemas.py           # Pydantic v2 request/response models: PositionCreate, PositionFilter, PositionResponse, ...
│   ├── updater.py           # PositionUpdater: Kafka consumer that applies ticks
│   ├── store.py             # PositionStore: Postgres queries, including the per-tick UPDATEs
│   ├── schema.sql           # positions table
│   ├── market_data.py       # SymbolsClient: subscribe a symbol in market-data-service
│   ├── models.py            # domain types: Position (a table row), Side, Status
│   ├── config.py            # URLs, topic, consumer group, symbol format, port
│   └── telemetry.py         # JSON logs; OTLP traces and metrics when configured
└── tests/
    ├── conftest.py          # fixtures: throwaway Postgres schema, store, factories, API client, in-memory OpenTelemetry SDK
    ├── fakes.py             # FakeSymbols, FakeConsumer/FakeMessage, tick_bytes()
    ├── test_store.py        # CRUD, entry and current-price rules (SQL)
    ├── test_api.py          # endpoints, validation, error codes, OpenAPI models
    ├── test_updater.py      # handle() and the Kafka poll loop
    ├── test_market_data.py  # SymbolsClient against httpx.MockTransport
    ├── test_telemetry.py    # JSON log format, setup(), pool gauges, instrumentation
    └── test_wiring.py       # from_url, lazy dependencies, main() entry points
```

## Not done yet

- **Closing a position.** `DELETE` removes the row. It doesn't record an exit price or a realised PnL.
- **Unsubscribing.** Deleting the last position in a symbol leaves that symbol subscribed in market-data-service, because the symbol may have been added there for another reason.
- **Unrealised PnL.** The API doesn't return it yet. It needs each contract's size: on Delta a BTC option contract is 0.001 BTC, so `(current_price − entry_price) × qty` alone would overstate it.
