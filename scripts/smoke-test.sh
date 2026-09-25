#!/usr/bin/env bash
# End-to-end smoke test of the running stack: infra, market-data-service's symbols API, and
# order-service (orders-api + position-updater). Run from anywhere once the stack is up:
#
#   scripts/smoke-test.sh
#
# It creates a buy position on a made-up symbol, publishes two fake ticks for that symbol to the
# real Kafka topic, and checks that the first sets entry_price (ask) and the second current_price
# (bid). The delta-ticker isn't needed. Cleanup deletes the position and unsubscribes the symbol.
set -euo pipefail

ORDERS_URL=${ORDERS_URL:-http://localhost:8001}
MARKET_DATA_URL=${MARKET_DATA_URL:-http://localhost:8000}
# A valid option symbol that Delta doesn't list, so it can't collide with real positions or ticks.
SYMBOL=${SMOKE_SYMBOL:-C-SMOKETEST-1-311299}
TOPIC=market-data.ticker

cd "$(dirname "$0")/.."
kafka() {  # kafka <tool.sh> [args...]: run one of Kafka's CLI tools inside the broker container
  local tool=$1; shift
  docker compose -f infra/docker-compose.yml exec -T kafka "/opt/kafka/bin/$tool" "$@"
}
json() { python3 -c "import json, sys; print(json.load(sys.stdin)$1)"; }
fail() { echo "FAIL: $*" >&2; exit 1; }

position_id=""
cleanup() {
  [ -n "$position_id" ] && curl -s -o /dev/null -X DELETE "$ORDERS_URL/positions/$position_id"
  curl -s -o /dev/null -X DELETE "$MARKET_DATA_URL/symbols/$SYMBOL"
}
trap cleanup EXIT

publish_tick() {  # publish_tick <seconds-from-now> <bid> <ask>
  python3 - "$SYMBOL" "$1" "$2" "$3" <<'EOF' | kafka kafka-console-producer.sh \
      --bootstrap-server localhost:9092 --topic "$TOPIC" --property parse.key=true --property key.separator='|'
import json, sys
from datetime import datetime, timedelta, timezone
symbol, offset, bid, ask = sys.argv[1], float(sys.argv[2]), float(sys.argv[3]), float(sys.argv[4])
time = (datetime.now(timezone(timedelta(hours=5, minutes=30))) + timedelta(seconds=offset)).isoformat()
payload = {"symbol": symbol, "product_id": 0, "strike_price": 1.0, "time": time, "spot_price": None,
           "mark_price": None, "best_bid": bid, "best_ask": ask, "delta": None}
print(f"{symbol}|{json.dumps(payload)}")
EOF
}

wait_for() {  # wait_for <python-expr on the position dict> <description>
  for _ in $(seq 1 30); do
    if curl -sf "$ORDERS_URL/positions/$position_id" | python3 -c "import json, sys; p = json.load(sys.stdin); sys.exit(0 if $1 else 1)"; then
      return 0
    fi
    sleep 1
  done
  curl -s "$ORDERS_URL/positions/$position_id" >&2; echo >&2
  fail "timed out waiting for $2"
}

echo "--- health"
curl -sf "$MARKET_DATA_URL/health" >/dev/null || fail "market-data-service /health"
curl -sf "$ORDERS_URL/health" >/dev/null || fail "order-service /health"

echo "--- validation"
code=$(curl -s -o /dev/null -w '%{http_code}' -X POST "$ORDERS_URL/positions" \
  -H 'content-type: application/json' -d '{"symbol": "BTCUSD", "side": "buy", "qty": 1}')
[ "$code" = 422 ] || fail "invalid symbol returned $code, expected 422"

echo "--- create (Postgres insert + market-data subscribe)"
created=$(curl -sf -X POST "$ORDERS_URL/positions" -H 'content-type: application/json' \
  -d "{\"symbol\": \"$SYMBOL\", \"side\": \"buy\", \"qty\": 1}") || fail "POST /positions"
position_id=$(echo "$created" | json '["id"]')
[ "$(echo "$created" | json '["status"]')" = pending ] || fail "new position isn't pending: $created"
curl -sf "$MARKET_DATA_URL/symbols/$SYMBOL" >/dev/null || fail "$SYMBOL wasn't subscribed in market-data-service"
echo "position $position_id created and $SYMBOL subscribed"

echo "--- entry tick via Kafka (buy enters at the ask)"
publish_tick 2 100 101
wait_for 'p["status"] == "open" and p["entry_price"] == 101.0 and p["current_price"] is None' "entry_price=101"

echo "--- next tick via Kafka (buy is marked at the bid)"
publish_tick 3 110 111
wait_for 'p["current_price"] == 110.0 and p["entry_price"] == 101.0' "current_price=110"

echo "--- delete"
code=$(curl -s -o /dev/null -w '%{http_code}' -X DELETE "$ORDERS_URL/positions/$position_id")
[ "$code" = 204 ] || fail "DELETE returned $code"
code=$(curl -s -o /dev/null -w '%{http_code}' "$ORDERS_URL/positions/$position_id")
[ "$code" = 404 ] || fail "GET after DELETE returned $code"
position_id=""

echo "PASS"
