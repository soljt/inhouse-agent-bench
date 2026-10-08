#!/usr/bin/env bash
# Runs inside the container. Starts the harness in the agent's working directory with the
# task prompt. Approval settings are the harness's own (opencode/opencode.json, codex/config.toml).
# Env: HARNESS=oc|cx, MODEL_REF (provider/model for oc), PROFILE and MODEL (for cx), PROMPT_FILE
# Scripted mode (TURNS_IN set): no TUI. The prompt is turn 1, then each TURNS_IN/NN.md is sent as a follow-up
# in the same session, with all permissions auto-approved (the container is the sandbox). After every turn the
# workspace diff and the agent's last message go to TURNS_OUT/NN.diff and NN.last.md.
# BENCH_YOLO=1: the interactive TUI is auto-approved too (Codex also skips its own sandbox).
set -euo pipefail
for d in /work/the app/angular /work/the app/TypeScript; do
  [ -d "$d/node_modules/." ] || { echo "$d/node_modules missing: run bench/setup-deps.sh on the host" >&2; exit 2; }
done
cd /work/pc
prompt="$(cat "$PROMPT_FILE")"

snapshot() { # turn number
  (cd /work && git add -A -N . 2>/dev/null; git diff; git reset -q) > "$TURNS_OUT/$1.diff" 2>/dev/null || true
}

if [ -n "${TURNS_IN:-}" ]; then
  turns=("$PROMPT_FILE"); for f in $(ls "$TURNS_IN"/*.md 2>/dev/null | sort); do turns+=("$f"); done
  n=0
  for f in "${turns[@]}"; do
    n=$((n + 1)); t=$(printf '%02d' $n); msg="$(cat "$f")"
    echo "===== turn $t: $(basename "$f")"
    case "$HARNESS" in
      oc)
        export OPENCODE_CONFIG=/harness/opencode/opencode.json
        cont=(); [ $n -gt 1 ] && cont=(--continue)
        opencode run --auto -m "$MODEL_REF" "${cont[@]}" "$msg" | tee "$TURNS_OUT/$t.out" || echo "turn $t exited non-zero" >> "$TURNS_OUT/errors.txt"
        ;;
      cx)
        export CODEX_HOME="$HOME/.codex"; mkdir -p "$CODEX_HOME"; cp -n /harness/codex/*.toml "$CODEX_HOME/"
        common=(--dangerously-bypass-approvals-and-sandbox --skip-git-repo-check -c "model=\"$MODEL\""
                -c "model_provider=\"$PROFILE\"" -c "model_providers.local.base_url=\"$LOCAL_BASE_URL\""
                -o "$TURNS_OUT/$t.last.md")
        if [ $n -eq 1 ]; then codex exec -p "$PROFILE" "${common[@]}" "$msg"
        else codex exec resume --last "${common[@]}" "$msg"; fi | tee "$TURNS_OUT/$t.out" || echo "turn $t exited non-zero" >> "$TURNS_OUT/errors.txt"
        ;;
      *) echo "HARNESS must be oc or cx" >&2; exit 2 ;;
    esac
    snapshot "$t"
  done
  exit 0
fi

case "$HARNESS" in
  oc)
    export OPENCODE_CONFIG=/harness/opencode/opencode.json
    yolo=(); [ "${BENCH_YOLO:-0}" = 1 ] && yolo=(--auto)
    exec opencode "${yolo[@]}" -m "$MODEL_REF" --prompt "$prompt" ;;
  cx)
    export CODEX_HOME="$HOME/.codex"; mkdir -p "$CODEX_HOME"; cp /harness/codex/*.toml "$CODEX_HOME/"
    yolo=(); [ "${BENCH_YOLO:-0}" = 1 ] && yolo=(--dangerously-bypass-approvals-and-sandbox)
    exec codex -p "$PROFILE" "${yolo[@]}" -c "model=\"$MODEL\"" \
      -c "model_providers.local.base_url=\"$LOCAL_BASE_URL\"" "$prompt" ;;
  *) echo "HARNESS must be oc or cx" >&2; exit 2 ;;
esac
