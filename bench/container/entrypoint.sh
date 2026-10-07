#!/usr/bin/env bash
# Runs inside the container. Starts the harness in the agent's working directory with the
# task prompt. Approval settings are the harness's own (opencode/opencode.json, codex/config.toml).
# Env: HARNESS=oc|cx, MODEL_REF (provider/model for oc), PROFILE and MODEL (for cx), PROMPT_FILE
set -euo pipefail
for d in /work/the app/angular /work/the app/TypeScript; do
  [ -d "$d/node_modules/." ] || { echo "$d/node_modules missing: run bench/setup-deps.sh on the host" >&2; exit 2; }
done
cd /work/pc
prompt="$(cat "$PROMPT_FILE")"
case "$HARNESS" in
  oc)
    export OPENCODE_CONFIG=/harness/opencode/opencode.json
    exec opencode -m "$MODEL_REF" --prompt "$prompt" ;;
  cx)
    export CODEX_HOME="$HOME/.codex"; mkdir -p "$CODEX_HOME"; cp /harness/codex/*.toml "$CODEX_HOME/"
    exec codex -p "$PROFILE" -c "model=\"$MODEL\"" \
      -c "model_providers.local.base_url=\"$LOCAL_BASE_URL\"" "$prompt" ;;
  *) echo "HARNESS must be oc or cx" >&2; exit 2 ;;
esac
