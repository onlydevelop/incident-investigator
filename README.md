# incident-investigator

Paper-trading stack for Delta Exchange options.

| Directory | What it is |
|---|---|
| [`infra/`](infra/README.md) | Shared Redis, Kafka and Postgres on the `portfolio` Docker network |
| [`market-data-service/`](market-data-service/README.md) | Streams Delta tickers to Kafka and Redis; symbols API on `:8000` |
| [`order-service/`](order-service/README.md) | Paper positions in Postgres, priced from the Kafka ticker feed; positions API on `:8001` |
| [`scripts/smoke-test.sh`](scripts/smoke-test.sh) | End-to-end check of a running stack |

To start everything, run `make -C order-service up`. That brings up the infra and market-data-service as well.

## CI

[`.github/workflows/ci.yml`](.github/workflows/ci.yml) runs on every push to `main`, every pull request, and on demand. A new push cancels the run already in progress for the same branch.

| Job | What it checks |
|---|---|
| Lint | `ruff check` with [`ruff.toml`](ruff.toml): pyflakes, likely bugs and import order. Also checks that every compose file is valid and runs shellcheck on `scripts/` |
| Test market-data-service | Unit tests with branch coverage. Fails below 80%; it's at 83% now |
| Test order-service | Unit tests against a real Postgres 18 service container, with branch coverage. Fails below 95%; it's at 100% now |
| Build | Builds each service's runtime image with a layer cache. Nothing is pushed |
| Smoke test | Runs after the four jobs above pass. Starts the real stack (infra, the symbols API, order-service) and runs `scripts/smoke-test.sh` |

The smoke test doesn't start `delta-ticker`, so CI never depends on Delta Exchange being reachable. It publishes its own ticks to Kafka instead. It creates a position, checks that the first tick sets `entry_price` from the ask and the next sets `current_price` from the bid, then deletes the position.

Each test job writes a coverage table to the run's summary page. It also uploads `coverage.xml`, the HTML report and `junit.xml` as artifacts, kept for 14 days.

[Dependabot](.github/dependabot.yml) opens weekly PRs for Python dependencies, base images and actions.

### Running the same checks locally

```sh
docker run --rm -v "$PWD":/src -w /src ghcr.io/astral-sh/ruff:0.16.9 check .   # lint
make -C market-data-service coverage
make -C order-service coverage             # starts infra if needed, for Postgres
make -C order-service up && scripts/smoke-test.sh
```

If `delta-ticker` is running while you run the smoke test, it may briefly try to subscribe the test symbol `C-SMOKETEST-1-311299` on Delta. To avoid that, stop it with `docker stop delta-ticker` first.
