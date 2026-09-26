---
id: RB-009
type: runbook
title: Pod CrashLoopBackOff / OOMKilled
services: [market-data-service, order-service]
components: [kubernetes, delta-ticker, orders-api, position-updater, kafka, redis]
alerts: [PodRestarting, ContainerOOMKilled]
symptoms: [restarts, gaps in metrics, Traefik no available server, pods not ready]
severity: sev2
last_reviewed: 2026-09-26
---

# RB-009: Pod CrashLoopBackOff / OOMKilled

## Alert
- `PodRestarting`: `increase(kube_pod_container_status_restarts_total{namespace="incident-investigator"}[15m]) > 3`.
- `ContainerOOMKilled`: `kube_pod_container_status_last_terminated_reason{namespace="incident-investigator",reason="OOMKilled"} == 1`.

## Triage
1. Why did it exit?
   ```bash
   kubectl -n incident-investigator get pods
   kubectl -n incident-investigator describe pod <pod> | grep -A5 "Last State"
   kubectl -n incident-investigator logs <pod> --previous | tail -50
   ```
2. Match the exit to a known cause in this stack:

| Exit | Cause |
|---|---|
| delta-ticker `Completed`, exit 0, restarts climbing | The websocket closed; `run()` returns and the process ends, because there is no reconnect loop (PM-001) |
| `OOMKilled` (exit 137) | Memory limit hit. Limits: apps 256 MiB, Redis 256 MiB, Postgres 512 MiB, Kafka 1 GiB |
| position-updater exit 1 with a psycopg traceback | It creates the schema on startup and exits if Postgres is unreachable; the init container normally prevents this |
| `CreateContainerConfigError` | An image built before the Dockerfiles switched to `USER 10001`; `make deploy` rebuilds it |
| `ErrImageNeverPull` | The `:local` image isn't built in this Docker context; run `make images` |

3. Probes: the APIs' liveness probe checks `/docs`, so a database outage never restarts them; it makes them unready instead (RB-004). delta-ticker and position-updater have no probes at all: a hung process is never restarted.
4. For OOM, look at the memory curve before the kill:
   ```promql
   container_memory_working_set_bytes{namespace="incident-investigator",container!=""}
   ```
   - Steady climb over hours: a leak. Leaked connection pools (PM-004) each hold a background thread and a connection.
   - Sudden jump: a burst, such as a large symbol list or a Kafka backlog.
   - Redis climbing: no `maxmemory` is set, so it grows until killed (RB-006).

## Mitigation
- Raise the memory limit to stop the loop, then fix the cause.
- CrashLoopBackOff waits up to 5 min between attempts; `kubectl -n incident-investigator delete pod <pod>` starts a fresh pod with no back-off once the cause is fixed.
- Startup errors after a deploy: roll back (RB-010).

## Verify
No restarts for 30 min and memory flat.
