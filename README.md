# incident-investigator

[![CI](../../actions/workflows/ci.yml/badge.svg?branch=main)](../../actions/workflows/ci.yml?query=branch%3Amain)
[![build](../../raw/badges/build.svg)](../../actions/workflows/ci.yml?query=branch%3Amain)
[![lint](../../raw/badges/lint.svg)](../../actions/workflows/ci.yml?query=branch%3Amain)
[![smoke test](../../raw/badges/smoke.svg)](../../actions/workflows/ci.yml?query=branch%3Amain)

[![market-data-service tests](../../raw/badges/tests-market-data-service.svg)](market-data-service/README.md)
[![market-data-service coverage](../../raw/badges/coverage-market-data-service.svg)](market-data-service/README.md)
[![order-service tests](../../raw/badges/tests-order-service.svg)](order-service/README.md)
[![order-service coverage](../../raw/badges/coverage-order-service.svg)](order-service/README.md)

Paper-trading stack for Delta Exchange options.

| Directory | What it is |
|---|---|
| [`infra/`](infra/README.md) | Shared Redis, Kafka and Postgres on the `portfolio` Docker network |
| [`market-data-service/`](market-data-service/README.md) | Streams Delta tickers to Kafka and Redis; symbols API on `:8000` |
| [`order-service/`](order-service/README.md) | Paper positions in Postgres, priced from the Kafka ticker feed; positions API on `:8001` |
| [`deploy/k8s/`](deploy/k8s/README.md) | The whole stack on the local k3s (Rancher Desktop), with Kustomize |
| [`scripts/smoke-test.sh`](scripts/smoke-test.sh) | End-to-end check of a running stack, on Compose or k3s |

## Running the stack

There are two ways to run the whole stack locally. They keep separate data and can run side by side, but together they use about 2 GB of memory, so usually run one at a time.

### On the local k3s (Rancher Desktop)

Run these from the repo root; `make help` lists them. They call the targets in [`deploy/k8s/Makefile`](deploy/k8s/Makefile).

| Command | What it does |
|---|---|
| `make k8s-start` | Start the stack and wait until every pod is ready. Images are built only if they don't exist yet. Works after `k8s-stop`, and on a fresh cluster |
| `make k8s-stop` | Scale every workload to 0. Config, ingress and data volumes are kept, so the next `k8s-start` comes back with the same positions and symbols |
| `make k8s-status` | Pods, services, ingress and volumes |
| `make k8s-deploy` | Rebuild both images and roll them out. Use this after changing code; `k8s-start` keeps the images it already has |

The APIs are then at <http://orders.localhost/docs> and <http://market-data.localhost/docs>.

These need `make -C deploy/k8s <target>`:

| Command | What it does |
|---|---|
| `smoke` | Run the end-to-end smoke test in the cluster |
| `logs APP=<name>` | Follow one workload's logs, e.g. `APP=position-updater` |
| `diagnose` | Show pods that aren't ready, and recent warning events |
| `delete` | Remove all workloads but keep the data volumes |
| `delete-all` | Remove the namespace **and all its data** |

For a start-to-finish example, see [the k3s walkthrough](deploy/k8s/README.md#walkthrough-deploy-trade-clean-up). It deploys the stack, creates positions and checks them, deletes them, and removes the stack. [`deploy/k8s/README.md`](deploy/k8s/README.md) has the rest of the details.

### With Docker Compose

| Command | What it does |
|---|---|
| `make -C order-service up` | Start everything: shared infra, market-data-service and order-service |
| `make -C order-service down` | Stop order-service. Infra and market-data-service keep running |
| `make -C market-data-service down` | Stop market-data-service |
| `make -C infra down` | Stop Redis, Kafka and Postgres. Data volumes are kept |

The APIs are then at <http://localhost:8001/docs> (orders) and <http://localhost:8000/docs> (market data). Each service's README lists the rest of its `make` targets.

## CI

[`.github/workflows/ci.yml`](.github/workflows/ci.yml) runs on every push to `main`, every pull request, and on demand. A new push cancels the run already in progress for the same branch.

| Job | What it checks |
|---|---|
| Lint | `ruff check` with [`ruff.toml`](ruff.toml): pyflakes, likely bugs and import order. Also checks that every compose file is valid, runs shellcheck on `scripts/`, and renders the k8s manifests with `kubectl kustomize` |
| Test market-data-service | Unit tests with branch coverage. Fails below 80%; it's at 83% now |
| Test order-service | Unit tests against a real Postgres 18 service container, with branch coverage. Fails below 95%; it's at 100% now |
| Build | Builds each service's runtime image with a layer cache. Nothing is pushed |
| Smoke test | Runs after the four jobs above pass. Starts the real stack (infra, the symbols API, order-service) and runs `scripts/smoke-test.sh` |
| Publish badges | Pushes and manual runs on `main` only. Turns the results above into the README badges (see [Badges](#badges)) |

The smoke test doesn't start `delta-ticker`, so CI never depends on Delta Exchange being reachable. It publishes its own ticks to Kafka instead. It creates a position, checks that the first tick sets `entry_price` from the ask and the next sets `current_price` from the bid, then deletes the position.

Each test job writes a coverage table to the run's summary page. It also uploads `coverage.xml`, the HTML report and `junit.xml` as artifacts, kept for 14 days.

### Badges

The badges at the top of this README come from the latest run on `main`.

- **CI** is GitHub's own badge for the whole workflow.
- **The others** are made by the `badges` job with [`scripts/badge.py`](scripts/badge.py), from that run's job results, JUnit files and `coverage.xml` files. They're force-pushed to the `badges` branch as a single commit, so they never add commits to `main`.
- **When a job fails,** its badge turns red. The tests badge then shows how many tests failed, and a coverage report that was never produced shows as grey "unknown".
- **Links are relative** (`../../raw/badges/...`), so they work in any fork or under any repo name, and in a private repo for anyone who can view it.
- **Before the first run on `main`,** the `badges` branch doesn't exist, so these images appear broken.

[Dependabot](.github/dependabot.yml) opens weekly PRs for Python dependencies, base images and actions.

### Running the same checks locally

```sh
docker run --rm -v "$PWD":/src -w /src ghcr.io/astral-sh/ruff:0.16.9 check .   # lint
make -C market-data-service coverage
make -C order-service coverage             # starts infra if needed, for Postgres
make -C order-service up && scripts/smoke-test.sh      # smoke test on Docker Compose
make k8s-start && make -C deploy/k8s smoke             # smoke test on k3s
```

On Docker Compose, if `delta-ticker` is running while you run the smoke test, it may briefly try to subscribe the test symbol `C-SMOKETEST-1-311299` on Delta. To avoid that, stop it with `docker stop delta-ticker` first. The k3s `smoke` target does this for you.
