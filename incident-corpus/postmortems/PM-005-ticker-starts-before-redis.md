---
id: PM-005
type: postmortem
title: Ticker starts before Redis and reports no symbols
basis: observed
date: 2026-09-26
duration_minutes: 5
severity: sev3
services: [market-data-service, order-service]
root_cause_category: startup-ordering
related_runbooks: [RB-006, RB-001]
---

# PM-005: Ticker starts before Redis and reports no symbols

## Summary
In the same stack start as PM-004, delta-ticker started at 01:22:15 UTC, before Redis accepted connections. `SymbolRegistry.get()` returns `None` on a Redis error and `main()` falls back to an empty list, so the ticker logged `Failed to read symbols from ticker:symbols: network:ConnectionError`, then `Loaded symbols from ticker:symbols: []` and `No symbols configured yet; waiting for the next refresh`. The symbol set was intact on Redis's AOF volume the whole time.

## Impact
- About 5 minutes with no ticks for the two subscribed symbols.
- No data lost; positions caught up on the first tick after recovery.

## Timeline (UTC, 2026-09-26)
- 01:22:15 First ticker pod: `symbols_read_failed`, `symbols_loaded` with `[]`, `ws_open`, `no_symbols`.
- 01:22:22 The `Recreate` rollout replaces it; the new pod logs the same sequence.
- 01:22 to 01:27 No further `symbols_read_failed` lines are logged.
- 01:27:22 `Symbols changed: +['C-BTC-80000-091026', 'P-BTC-80000-091026'] -[]`, exactly 5 minutes after startup. Ticks resume.

## Root cause
A failed read at startup is treated the same as an empty symbol list. Unlike position-updater, delta-ticker has no init container that waits for its dependency.

## What went wrong
- The log said `No symbols configured yet`, pointing the operator at the symbol list instead of at Redis.
- The Redis client has no socket timeout, so a refresh probably blocked on a connection attempt instead of failing fast and retrying every 30 s. This explains the silence until 01:27 but is not confirmed.

## Action items
- Retry the initial symbol read until Redis answers, or add an init container like position-updater's. Open.
- Set `socket_timeout` and `socket_connect_timeout` on the Redis client. Open.
- RB-001 and RB-006 describe this log sequence as a Redis problem, not an empty list. Done.
