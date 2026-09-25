---
id: RB-004
type: runbook
title: Order reject rate spike
services: [order-risk-service, market-data-service]
components: [redis, config]
alerts: [OrderRejectRateHigh]
symptoms: [orders rejected, desk cannot trade, health checks green]
severity: sev1
last_reviewed: 2026-09-25
---

# RB-004: Order reject rate spike

## Alert
`OrderRejectRateHigh`: non-margin rejects above 1% of orders for 5 min.

This is often a "quiet" incident: every pod is healthy and latency is fine, but orders fail. Treat it as sev1 because the desk cannot trade.

## Triage
1. Break down by reason; the reason almost always points to the cause.
   ```promql
   sum by (reason) (rate(order_rejects_total[5m]))
   ```

| Reason | Meaning | Go to |
|---|---|---|
| `STALE_PRICE` | Price older than 2 s | RB-001, RB-012 |
| `NO_PRICE` | Redis key missing | RB-006 |
| `RISK_TIMEOUT` | Risk check exceeded deadline | RB-003, RB-005 |
| `LIMIT_EXCEEDED` | Order over configured risk limit | Check config (PM-002) |
| `INSUFFICIENT_MARGIN` | Expected business reject | Only investigate if rate jumped with no market move |

2. Did anything change? Check recent deploys and ConfigMap edits:
   ```bash
   kubectl -n trading rollout history deploy/order-risk-service
   kubectl -n trading get configmap risk-limits -o yaml
   ```
3. Sample rejected orders in logs:
   ```logql
   {service="order-risk-service"} | json | event="order_rejected" | line_format "{{.reason}} {{.symbol}} {{.order_id}}"
   ```
   Check if rejects cluster on specific symbols, sizes, or one client.

## Mitigation
Follow the runbook for the dominant reason. For a bad config or release, roll back first and investigate after (RB-010).

## Verify
Reject rate by reason back to baseline for 15 min.
