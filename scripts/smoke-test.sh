#!/usr/bin/env bash
# Checks that an endpoint returns a well-formed tool call on both APIs the
# harnesses use: Chat Completions (OpenCode) and Responses (Codex).
# Usage: scripts/smoke-test.sh [local|dryrun]
set -euo pipefail
REPO="$(cd "$(dirname "$(readlink -f "$0")")/.." && pwd)"
[ -f "$REPO/.env" ] && { set -a; . "$REPO/.env"; set +a; }

if [ "${1:-local}" = dryrun ]; then
  base="https://api.openai.com/v1"; key="$OPENAI_API_KEY"; model="$DRYRUN_MODEL"
else
  base="$LOCAL_BASE_URL"; key="$LOCAL_API_KEY"; model="$LOCAL_MODEL"
fi
prompt="What is the weather in Zurich? Use the tool."
params='{"type":"object","properties":{"city":{"type":"string"}},"required":["city"]}'

post() { curl -sS --max-time 120 "$base/$1" -H "Authorization: Bearer $key" -H "Content-Type: application/json" -d "$2"; }

echo "== $base  model=$model"
echo "-- /chat/completions (OpenCode)"
post chat/completions "{\"model\":\"$model\",\"messages\":[{\"role\":\"user\",\"content\":\"$prompt\"}],
  \"tools\":[{\"type\":\"function\",\"function\":{\"name\":\"get_weather\",\"parameters\":$params}}]}" |
python3 -c '
import json,sys; r=json.load(sys.stdin)
if "error" in r: sys.exit("FAIL: %s" % r["error"])
calls=r["choices"][0]["message"].get("tool_calls") or []
ok=calls and calls[0]["function"]["name"]=="get_weather" and "city" in json.loads(calls[0]["function"]["arguments"])
print("PASS" if ok else "FAIL: no valid tool call:\n"+json.dumps(r["choices"][0]["message"],indent=1)); sys.exit(0 if ok else 1)'

echo "-- /responses (Codex)"
post responses "{\"model\":\"$model\",\"input\":\"$prompt\",
  \"tools\":[{\"type\":\"function\",\"name\":\"get_weather\",\"parameters\":$params}]}" |
python3 -c '
import json,sys; r=json.load(sys.stdin)
if r.get("error"): sys.exit("FAIL: %s" % r["error"])
calls=[o for o in r.get("output",[]) if o.get("type")=="function_call"]
ok=calls and calls[0]["name"]=="get_weather" and "city" in json.loads(calls[0]["arguments"])
print("PASS" if ok else "FAIL: no valid tool call:\n"+json.dumps(r.get("output"),indent=1)); sys.exit(0 if ok else 1)'
