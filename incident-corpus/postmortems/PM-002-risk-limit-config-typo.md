---
id: PM-002
type: postmortem
title: Risk-limit config typo blocks all large orders
date: 2026-06-03
duration_minutes: 52
severity: sev1
services: [order-risk-service]
root_cause_category: config-change
related_runbooks: [RB-004, RB-010]
---

# PM-002: Risk-limit config typo blocks all large orders

## Summary
A ConfigMap change meant to raise the per-order notional limit for BTC options from 5 to 50 BTC set it to 0.5 BTC instead. Every order above 0.5 BTC notional was rejected with `LIMIT_EXCEEDED`. All pods, latency, and error-rate dashboards stayed green.

## Impact
- 52 minutes; 71% of orders rejected (all orders above 0.5 BTC).
- Small orders were unaffected, which delayed recognition that anything was wrong.

## Timeline (UTC)
- 13:05 `risk-limits` ConfigMap applied; order-risk pods restarted to load it.
- 13:07 `LIMIT_EXCEEDED` rejects rise from about 0.1% to 71% of orders.
- 13:07 No alert fires: the reject-rate alert excluded all "business" reasons, including `LIMIT_EXCEEDED`.
- 13:48 Desk reports large orders failing.
- 13:53 On-call breaks rejects down by reason; sees `LIMIT_EXCEEDED`.
- 13:55 ConfigMap diff shows `max_order_notional_btc: 0.5`.
- 13:57 Previous ConfigMap re-applied from Git; pods restarted; rejects normal.

## Root cause
Unit confusion: the author thought the value was in tens of BTC. The config had no validation and no review for value changes.

## What went wrong
- Alerting treated `LIMIT_EXCEEDED` as always legitimate.
- `kubectl rollout undo` does not revert ConfigMaps; the first rollback attempt at 13:54 did nothing.

## Action items
- Startup validation: reject limits outside a sane range (1 to 500 BTC). Done.
- Alert on any single reject reason jumping 10 times above its 7-day baseline. Done.
- Config changes require review, like code. Done.
- RB-010 documents ConfigMap rollback separately. Done.
