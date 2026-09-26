---
id: RB-004
type: runbook
title: orders-api error spike (502 / 503)
services: [order-service, market-data-service]
components: [orders-api, symbols-api, postgresql, ingress]
alerts: [OrdersApiErrorRateHigh]
symptoms: [POST /positions fails, 502 from orders-api, 503 Postgres unavailable, Traefik no available server]
severity: sev2
last_reviewed: 2026-09-26
---

# RB-004: orders-api error spike (502 / 503)

## Alert
`OrdersApiErrorRateHigh`: 5xx above 5% of orders-api requests for 5 min.
```promql
sum(rate(http_server_request_duration_seconds_count{job="incident-investigator/orders-api",http_response_status_code=~"5.."}[5m]))
  / sum(rate(http_server_request_duration_seconds_count{job="incident-investigator/orders-api"}[5m]))
```

Existing positions keep updating during orders-api errors, because the position-updater doesn't depend on orders-api. The impact is on creating and reading positions.

## Triage
1. Break down by status code; the code almost always points to the cause.

| Response | Meaning | Go to |
|---|---|---|
| `502` `Couldn't subscribe ... so no position was created` | symbols-api unreachable or returned non-2xx | Check symbols-api, then RB-006 |
| `503` `Postgres unavailable: couldn't get a connection after 5.00 sec` | Pool timeout: Postgres down or pool exhausted | RB-005 |
| `503` from Traefik with no JSON body | No ready orders-api pod: readiness `/health` failing | RB-009 |
| `422` | Client input, e.g. an unknown field or a bad symbol | Not an incident |

   A 502 is all or nothing: the insert was rolled back, so the client can simply retry.
2. Sample the failures in logs:
   ```logql
   {service_name="orders-api"} | json | event=~"position_rejected|postgres_unavailable"
   ```
   `position_rejected` carries `reason="market_data_unavailable"` and the symbol.
3. Check symbols-api directly: `curl http://market-data.localhost/health`. It returns 503 when Redis is unreachable, and orders-api then turns every create into a 502.
4. Did anything change? Check recent rollouts:
   ```bash
   kubectl -n incident-investigator rollout history deploy/orders-api
   kubectl -n incident-investigator get events --sort-by=.lastTimestamp | tail -20
   ```
   An error spike right after `make deploy` goes to RB-010.

## Mitigation
Follow the runbook for the dominant code. For a bad release, roll back first and investigate after (RB-010).

## Verify
5xx rate below 1% for 15 min, and a test `POST /positions` returns 201 with status `pending`.
