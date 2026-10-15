#!/usr/bin/env bash
# Checks that an endpoint returns a well-formed tool call on the APIs the harnesses use: Chat Completions
# (OpenCode on the local server) and Responses (Codex; OpenCode on OpenAI, whose adapter uses Responses).
# On OpenAI only Responses is checked, at reasoning effort high as in the harness configs: OpenAI's newer
# models refuse function tools with reasoning on Chat Completions.
# Usage: scripts/smoke-test.sh [local|openai]   (dryrun = openai)
set -euo pipefail
REPO="$(cd "$(dirname "$(readlink -f "$0")")/.." && pwd)"
. "$REPO/scripts/env.sh" "$REPO/.env"

if is_openai_target "${1:-local}"; then
  base="https://api.openai.com/v1"; key="$OPENAI_API_KEY"; model="$OPENAI_MODEL"
  chat=0; reasoning='"reasoning":{"effort":"high"},'
else
  base="$LOCAL_BASE_URL"; key="$LOCAL_API_KEY"; model="$LOCAL_MODEL"
  chat=1; reasoning=''
fi
prompt="What is the weather in Zurich? Use the tool."
params='{"type":"object","properties":{"city":{"type":"string"}},"required":["city"]}'

post() { curl -sS --max-time 120 "$base/$1" -H "Authorization: Bearer $key" -H "Content-Type: application/json" -d "$2"; }

echo "== $base  model=$model"
if [ "$chat" = 1 ]; then
echo "-- /chat/completions (OpenCode)"
post chat/completions "{\"model\":\"$model\",\"messages\":[{\"role\":\"user\",\"content\":\"$prompt\"}],
  \"tools\":[{\"type\":\"function\",\"function\":{\"name\":\"get_weather\",\"parameters\":$params}}]}" |
python3 -c '
import json,sys; r=json.load(sys.stdin)
if "error" in r: sys.exit("FAIL: %s" % r["error"])
calls=r["choices"][0]["message"].get("tool_calls") or []
ok=calls and calls[0]["function"]["name"]=="get_weather" and "city" in json.loads(calls[0]["function"]["arguments"])
print("PASS" if ok else "FAIL: no valid tool call:\n"+json.dumps(r["choices"][0]["message"],indent=1)); sys.exit(0 if ok else 1)'
fi

echo "-- /responses (Codex$([ "$chat" = 1 ] || echo ' and OpenCode'))"
post responses "{\"model\":\"$model\",$reasoning\"input\":\"$prompt\",
  \"tools\":[{\"type\":\"function\",\"name\":\"get_weather\",\"parameters\":$params}]}" |
python3 -c '
import json,sys; r=json.load(sys.stdin)
if r.get("error"): sys.exit("FAIL: %s" % r["error"])
calls=[o for o in r.get("output",[]) if o.get("type")=="function_call"]
ok=calls and calls[0]["name"]=="get_weather" and "city" in json.loads(calls[0]["arguments"])
print("PASS" if ok else "FAIL: no valid tool call:\n"+json.dumps(r.get("output"),indent=1)); sys.exit(0 if ok else 1)'
