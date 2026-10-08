#!/usr/bin/env bash
# One benchmark run: reset workspace, check network isolation, start the harness TUI in the
# container with the task prompt, then grade and keep the transcript.
# Usage: bench/run.sh <oc|cx> <local|dryrun> <task-id>
#   e.g. bench/run.sh oc dryrun e1-word-count
set -euo pipefail
BENCH="$(cd "$(dirname "$(readlink -f "$0")")" && pwd)"; REPO="$(dirname "$BENCH")"
WS="${BENCH_WORKSPACE:-$HOME/llm-bench}"; DEPS="${BENCH_DEPS:-$HOME/llm-bench-deps}"
BRIDGE_IP=172.17.0.1; PROXY_PORT=3128
harness="$1"; target="$2"; task="$3"
TASK="$BENCH/tasks/$task"; [ -d "$TASK" ] || { echo "no task $task" >&2; exit 2; }
# BENCH_ENV: another env file (e.g. a second model server); BENCH_RESULTS: another results folder
set -a; . "${BENCH_ENV:-$REPO/.env}"; . "$TASK/task.env"; set +a
RESULTS="${BENCH_RESULTS:-$BENCH/results}"

RUN_NET=(--add-host host.docker.internal:host-gateway -v "$DEPS:$DEPS")

# The container must have no direct internet. Abort rather than run unconfined.
code=$(docker run --rm "${RUN_NET[@]}" --entrypoint curl llm-bench -s -o /dev/null -w '%{http_code}' --max-time 8 https://github.com || true)
[ "$code" = "000" ] || { echo "Container reached github.com directly (HTTP $code). Fix bridge egress first." >&2; exit 3; }

proxy_env=()
if [ "$target" = dryrun ]; then
  model="$DRYRUN_MODEL"; profile=dryrun; base_url="$LOCAL_BASE_URL"
  if ! (exec 3<>/dev/tcp/$BRIDGE_IP/$PROXY_PORT) 2>/dev/null; then
    mkdir -p "$BENCH/results"
    nohup python3 "$BENCH/egress-proxy.py" --listen "$BRIDGE_IP:$PROXY_PORT" --allow api.openai.com:443 \
      --log "$BENCH/results/egress.log" >/dev/null 2>&1 &
    sleep 1
  fi
  proxy_env=(-e "HTTPS_PROXY=http://host.docker.internal:$PROXY_PORT" -e "https_proxy=http://host.docker.internal:$PROXY_PORT"
             -e "NO_PROXY=host.docker.internal,localhost" -e "no_proxy=host.docker.internal,localhost")
else
  model="$LOCAL_MODEL"; profile=local
  base_url="${LOCAL_BASE_URL/localhost/host.docker.internal}"; base_url="${base_url/127.0.0.1/host.docker.internal}"
  # An SSH tunnel on localhost only is invisible to containers; forward the bridge port to it
  tport="${LOCAL_BASE_URL##*:}"; tport="${tport%%/*}"
  if ! (exec 3<>/dev/tcp/$BRIDGE_IP/$tport) 2>/dev/null; then
    mkdir -p "$BENCH/results"
    nohup python3 "$BENCH/egress-proxy.py" --listen "$BRIDGE_IP:$tport" --forward "127.0.0.1:$tport" \
      --log "$BENCH/results/egress.log" >/dev/null 2>&1 &
    sleep 1
  fi
  code=$(docker run --rm "${RUN_NET[@]}" --entrypoint curl llm-bench -s -o /dev/null -w '%{http_code}' --max-time 8 \
    -H "Authorization: Bearer $LOCAL_API_KEY" "$base_url/models" || true)
  [ "$code" = "200" ] || { echo "Model endpoint $base_url not reachable from the container (HTTP $code). Is the SSH tunnel up?" >&2; exit 3; }
fi

# Java and Maven take the home directory from /etc/passwd, not $HOME, so mount the run's home
# where the image's passwd entry for this uid points (uid 1000 is "node" in the image)
AGENT_HOME=$(docker run --rm --user "$(id -u):$(id -g)" --entrypoint sh llm-bench -c 'getent passwd "$(id -u)" | cut -d: -f6')
AGENT_HOME="${AGENT_HOME:-/home/agent}"

# BENCH_RUN_SUFFIX keeps concurrent runs (bench/load.sh) that start in the same second apart
RUN="$RESULTS/$task/$harness-$target-${model//\//_}-$(date +%y%m%d_%H%M%S)${BENCH_RUN_SUFFIX:+-$BENCH_RUN_SUFFIX}"
mkdir -p "$RUN/home"; cp "$TASK/prompt.md" "$RUN/prompt.md"
"$BENCH/reset.sh" "$BASE_TAG" "$WS"
[ -x "$TASK/prepare.sh" ] && "$TASK/prepare.sh" "$WS"
mkdir -p "$RUN/tomcat/lib"
printf '[user]\n\tname = Bench Agent\n\temail = agent@bench.example\n' > "$RUN/home/.gitconfig"
registry_pid=""
if [ "${NEEDS_REGISTRY:-0}" = 1 ]; then
  mkdir -p "$RUN/home/.m2"; cp "$BENCH/container/maven-settings.xml" "$RUN/home/.m2/settings.xml"
  python3 "$BENCH/maven-repo.py" --listen "$BRIDGE_IP:8081" --seed "$DEPS/maven-seed" \
    --store "$RUN/registry" --log "$RUN/registry.log" & registry_pid=$!
  sleep 1
fi
{ echo "task=$task"; echo "harness=$harness"; echo "target=$target"; echo "model=$model"
  echo "base_tag=$BASE_TAG"; echo "workspace=$WS"; echo "start=$(date -Is)"
  for kv in ${BENCH_META:-}; do echo "$kv"; done; } > "$RUN/meta.txt"

# Scripted tasks (a turns/ folder) run without a TUI: follow-ups are sent in the same session, see entrypoint.sh
tty=(-it); scripted=()
# BENCH_SCRIPTED=1 runs a single-prompt task the same way, without a TUI (bench/load.sh does this)
if [ -d "$TASK/turns" ] || [ "${BENCH_SCRIPTED:-0}" = 1 ]; then
  turns_in="$TASK/turns"; [ -d "$turns_in" ] || { turns_in="$RUN/no-turns"; mkdir -p "$turns_in"; }
  mkdir -p "$RUN/turns"; tty=()
  scripted=(-v "$turns_in:/run/turns-in:ro" -v "$RUN/turns:/run/turns-out" -e TURNS_IN=/run/turns-in -e TURNS_OUT=/run/turns-out)
  echo "scripted=1" >> "$RUN/meta.txt"
fi
docker_run() { docker run --rm "${tty[@]}" --name "bench-$task-$$" "${RUN_NET[@]}" "${scripted[@]}" \
  -v "$BENCH/container/entrypoint.sh:/usr/local/bin/entrypoint.sh:ro" \
  --user "$(id -u):$(id -g)" -e HOME="$AGENT_HOME" -v "$RUN/home:$AGENT_HOME" \
  -v "$WS:/work" -v "$RUN/prompt.md:/run/prompt.md:ro" -v "$RUN/tomcat:/usr/share/tomcat" \
  -v "$REPO/opencode:/harness/opencode:ro" -v "$REPO/codex:/harness/codex:ro" \
  -e HARNESS="$harness" -e PROMPT_FILE=/run/prompt.md \
  -e MODEL_REF="$profile/$model" -e PROFILE="$profile" -e MODEL="$model" \
  -e LOCAL_BASE_URL="$base_url" -e LOCAL_API_KEY -e LOCAL_MODEL \
  -e OPENAI_API_KEY -e DRYRUN_MODEL "${proxy_env[@]}" \
  llm-bench; }
if [ ${#tty[@]} -eq 0 ]; then docker_run 2>&1 | tee "$RUN/console.log" || true; else docker_run || true; fi

echo "end=$(date -Is)" >> "$RUN/meta.txt"
[ -n "$registry_pid" ] && kill "$registry_pid" 2>/dev/null
"$TASK/grade.sh" "$RUN/grade" "$WS" ${GRADE_WITH_NG:+--with-ng} || true
python3 "$BENCH/score.py" "$RUN" || true
echo "Run saved in $RUN. Transcript: $RUN/home (OpenCode: .local/share/opencode, Codex: .codex/sessions)"
echo "Grade it: bench/review.py $RUN   (or later: bench/review.py next)"
