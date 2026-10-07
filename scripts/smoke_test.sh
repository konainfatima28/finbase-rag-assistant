#!/usr/bin/env bash
# Post-deploy smoke test:  scripts/smoke_test.sh https://finbase-api.onrender.com
# Checks /api/health, one answerable question, one not-found question, and the SSE stream.
set -euo pipefail
API="${1:-${API_URL:-http://localhost:8000}}"
API="${API%/}"
fail() { echo "FAIL: $*" >&2; exit 1; }
json() { python -c "import sys,json; d=json.load(sys.stdin); print($1)"; }

echo "1/4 health (allowing up to 90 s for a cold start)"
for i in $(seq 1 18); do
  if body=$(curl -fsS --max-time 10 "$API/api/health"); then break; fi
  sleep 5
done
[ -n "${body:-}" ] || fail "health did not respond"
echo "$body" | json "d['status'], d['provider'], d['chat_model'], d['embed_model'], d['chunks']"

echo "2/4 answerable question"
ans=$(curl -fsS --max-time 60 -H 'Content-Type: application/json' -d '{"message":"What is the foreclosure charge if I close my personal loan after 18 months?"}' "$API/api/chat")
echo "$ans" | json "d['answerable'], d['confidence']['label'], d['sources'][0]['citation'] if d['sources'] else None"
[ "$(echo "$ans" | json "d['answerable'] and bool(d['sources'])")" = "True" ] || fail "expected a grounded answer with sources"

echo "3/4 not-in-knowledge-base question"
nf=$(curl -fsS --max-time 60 -H 'Content-Type: application/json' -d '{"message":"What is the FinBase home loan interest rate?"}' "$API/api/chat")
[ "$(echo "$nf" | json "d['answerable']")" = "False" ] || fail "expected abstention"
echo "abstained: $(echo "$nf" | json "d['answer'][:80]")"

echo "4/4 SSE stream"
sse=$(curl -fsS -N --max-time 60 -H 'Content-Type: application/json' -H 'Accept: text/event-stream' -d '{"message":"What is the UPI daily limit?","stream":true}' "$API/api/chat")
echo "$sse" | grep -q '^event: meta' || fail "no meta event"
echo "$sse" | grep -q '^event: token' || fail "no token event"
echo "$sse" | grep -q '^event: done' || fail "no done event"
echo "SSE events: $(echo "$sse" | grep -c '^event:')"
echo "SMOKE TEST PASSED"
