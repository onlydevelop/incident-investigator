# infra

The shared infrastructure for every service in this repo: Redis, Kafka and Postgres, run as one Docker Compose project called `infra`.

Each service keeps its own `docker-compose.yml` with only its app containers. Those join the `portfolio` Docker network, which this stack creates, and reach the infrastructure by service name.

```
                     portfolio network
  ┌──────────────────────────────────────────────────────────┐
  │                                                          │
  │  market-data-service ──┐                                 │
  │  (delta-ticker,        ├──► redis:6379     infra-redis   │ ◄── localhost:6379
  │   symbols-api)         │                                 │
  │                        ├──► kafka:9092     infra-kafka   │ ◄── localhost:9094
  │  order-service ────────┤                                 │
  │  (planned)             └──► postgres:5432  infra-postgres│ ◄── localhost:5432
  │                                                          │
  └──────────────────────────────────────────────────────────┘
```

## Quick start

```sh
cd infra
make up        # start redis, kafka and postgres, wait until healthy, create the Kafka topics
make status
make down      # stop and remove the containers; the data volumes are kept
```

You usually don't need to run this yourself. Each service's `make up` (and its `*-start` targets) runs `make up` here first, and it does nothing if everything is already running. A service's `make down` stops only that service's containers, so the infrastructure keeps running for the others.

## What runs

| Service | Container | From other containers | From the host | Data volume |
|---|---|---|---|---|
| Redis 8 (AOF persistence) | `infra-redis` | `redis://redis:6379/0` | `localhost:6379` | `infra_redis-data` |
| Kafka 4.1 (single-node KRaft, no ZooKeeper) | `infra-kafka` | `kafka:9092` | `localhost:9094` | `infra_kafka-data` |
| Postgres 18 | `infra-postgres` | `postgres://portfolio:portfolio@postgres:5432/portfolio` | `localhost:5432` | `infra_postgres-data` |

Kafka has two listeners because a client connects to whatever address the broker advertises. Containers must use `kafka:9092`, and tools on your machine must use `localhost:9094`. Using the wrong one connects at first and then fails with the other address.

## Makefile commands

Run `make` with no arguments to list every target.

| Command | Description |
|---|---|
| `make up` | Start everything, wait until healthy, then create the topics in `TOPICS` |
| `make down` | Stop and remove the containers. The data volumes are kept |
| `make restart` | Restart the containers |
| `make status` | Show the containers |
| `make logs` | Follow all logs. `make logs SERVICE=kafka` follows one |
| `make redis-cli` | Open `redis-cli` |
| `make psql` | Open `psql` on the `portfolio` database |
| `make topics-create` | Create any missing topics from `TOPICS` |
| `make topics-list` | List topics |
| `make topic-describe TOPIC=<topic>` | Show partitions, leader and config for one topic |
| `make topic-tail TOPIC=<topic>` | Print new messages with their keys. Ctrl+C to stop |

```sh
$ make up
 Container infra-postgres Healthy
 Container infra-redis Healthy
 Container infra-kafka Healthy
topic market-data.ticker

$ make topic-describe TOPIC=market-data.ticker
Topic: market-data.ticker  PartitionCount: 3  ReplicationFactor: 1  Configs: min.insync.replicas=1
```

## Kafka topics

Auto-creation is turned off, so producing to a misspelt topic fails instead of quietly creating a new one. Topics are listed in `TOPICS` in the [Makefile](Makefile) as `<name>:<partitions>`, and `make up` creates any that are missing. It never changes or deletes an existing topic.

| Topic | Partitions | Producer | Consumer | Key / value |
|---|---|---|---|---|
| `market-data.ticker` | 3 | market-data-service (`delta-ticker`), one message per update | order-service (planned), to mark positions to market | Symbol / `TickerPayload` JSON |

To add a topic, add it to `TOPICS` and run `make topics-create`.

## Configuration

These are environment variables read by [`docker-compose.yml`](docker-compose.yml) and the Makefile.

| Variable | Default | Notes |
|---|---|---|
| `REDIS_PORT` | `6379` | Host port for Redis |
| `KAFKA_PORT` | `9094` | Host port for Kafka. It is also the advertised host address, so both change together |
| `POSTGRES_PORT` | `5432` | Host port for Postgres |
| `POSTGRES_USER` / `POSTGRES_PASSWORD` / `POSTGRES_DB` | `portfolio` / `portfolio` / `portfolio` | These are only applied when the volume is first created. Changing them later has no effect unless you recreate `infra_postgres-data` |

The credentials are for local development only.

## Resetting data

`make down` keeps all data. To wipe a store, stop everything and remove its volume:

```sh
make down
docker volume rm infra_kafka-data      # or infra_redis-data, infra_postgres-data
make up
```

Removing `infra_redis-data` also deletes market-data-service's symbol list.

With Rancher Desktop, a raw `docker` command needs `export DOCKER_HOST=unix://$HOME/.rd/docker.sock` first. The Makefile sets this for you.
