#!/usr/bin/env bash
# One benchmark run: reset workspace, check network isolation, start the harness TUI in the
# container with the task prompt, then grade and keep the transcript.
# Usage: bench/run.sh <oc|cx> <local|openai|chatgpt> <task-id>   (dryrun = openai; chatgpt: cx only)
#   e.g. bench/run.sh oc openai e1-word-count
set -euo pipefail
BENCH="$(cd "$(dirname "$(readlink -f "$0")")" && pwd)"; REPO="$(dirname "$BENCH")"
BRIDGE_IP=172.17.0.1; PROXY_PORT=3128; CHATGPT_PROXY_PORT=3129
harness="$1"; target="$2"; task="$3"
# BENCH_ENV: another env file (e.g. a second model server); BENCH_RESULTS: another results folder
# BENCH_VARIANT: a label for anything else that differs between runs (vLLM settings, harness version);
# report.py keeps each label in its own row. BENCH_YOLO=1 auto-approves the TUI and is keyed the same way.
. "$REPO/scripts/env.sh" "${BENCH_ENV:-$REPO/.env}"
case "$target" in local|openai|dryrun|chatgpt) ;; *) echo "target must be local, openai or chatgpt, not $target" >&2; exit 2 ;; esac
[ "$target" = chatgpt ] && [ "$harness" != cx ] && { echo "target chatgpt runs Codex only (a ChatGPT login is for Codex)" >&2; exit 2; }
# Runs record the target's current name
is_openai_target "$target" && target=openai
# The task set (BENCH_TASKSET): its image, workspace, deps and the agent's start folder (see lib/taskset.py)
eval "$(python3 "$BENCH/lib/taskset.py" env)"
WS="$TASKSET_WORKSPACE"; DEPS="$TASKSET_DEPS"; IMAGE="$TASKSET_IMAGE"
TASK="$TASKSET/tasks/$task"; [ -d "$TASK" ] || { echo "no task $task in $TASKSET" >&2; exit 2; }
set -a; . "$TASK/task.env"; set +a
RESULTS="${BENCH_RESULTS:-$BENCH/results}"

# Agents join bench-agents: no route out and no host address on it. Its only other member is bench-gateway,
# called host.docker.internal there, which relays just these ports to the host (see agentnet.py).
# The deps are shared by every run, so read-only: an agent cannot change what later runs (or grading) execute.
MODEL_PORT="${LOCAL_BASE_URL##*:}"; MODEL_PORT="${MODEL_PORT%%/*}"
python3 "$BENCH/agentnet.py" ensure "$MODEL_PORT" "$PROXY_PORT" "$CHATGPT_PROXY_PORT" 8081
RUN_NET=(--network bench-agents); [ -n "$DEPS" ] && RUN_NET+=(-v "$DEPS:$DEPS:ro")

# The container must have no direct internet and reach nothing on the host but the relayed ports. Abort rather than run unconfined.
code=$(docker run --rm "${RUN_NET[@]}" --entrypoint curl "$IMAGE" -s -o /dev/null -w '%{http_code}' --max-time 8 https://github.com || true)
[ "$code" = "000" ] || { echo "Container reached github.com directly (HTTP $code). Fix bridge egress first." >&2; exit 3; }
if docker run --rm "${RUN_NET[@]}" --entrypoint bash "$IMAGE" -c \
     "timeout 3 bash -c 'exec 3<>/dev/tcp/host.docker.internal/22' || timeout 3 bash -c 'exec 3<>/dev/tcp/$BRIDGE_IP/22'" 2>/dev/null; then
  echo "Container reached the host's ssh port. The agent network is not isolated (see agentnet.py)." >&2; exit 3
fi

proxy_env=(); provider=""; api_key_env=(-e OPENAI_API_KEY)
if [ "$target" = chatgpt ]; then
  # Codex with a ChatGPT login talks to chatgpt.com and refreshes the login at auth.openai.com. Its own proxy
  # port keeps that allowlist apart from the openai target's. The API key stays out of these containers.
  [ -n "$CHATGPT_MODEL" ] || { echo "CHATGPT_MODEL is not set (.env)" >&2; exit 2; }
  model="$CHATGPT_MODEL"; profile=chatgpt; provider=openai; base_url="$LOCAL_BASE_URL"; api_key_env=()
  if ! (exec 3<>/dev/tcp/$BRIDGE_IP/$CHATGPT_PROXY_PORT) 2>/dev/null; then
    mkdir -p "$BENCH/results"
    nohup python3 "$BENCH/egress-proxy.py" --listen "$BRIDGE_IP:$CHATGPT_PROXY_PORT" \
      --allow chatgpt.com:443 --allow auth.openai.com:443 --log "$BENCH/results/egress.log" >/dev/null 2>&1 &
    sleep 1
  fi
  proxy_env=(-e "HTTPS_PROXY=http://host.docker.internal:$CHATGPT_PROXY_PORT" -e "https_proxy=http://host.docker.internal:$CHATGPT_PROXY_PORT"
             -e "NO_PROXY=host.docker.internal,localhost" -e "no_proxy=host.docker.internal,localhost")
elif [ "$target" = openai ]; then
  model="$OPENAI_MODEL"; profile=openai-api; base_url="$LOCAL_BASE_URL"
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
  tport="$MODEL_PORT"
  if ! (exec 3<>/dev/tcp/$BRIDGE_IP/$tport) 2>/dev/null; then
    mkdir -p "$BENCH/results"
    nohup python3 "$BENCH/egress-proxy.py" --listen "$BRIDGE_IP:$tport" --forward "127.0.0.1:$tport" \
      --log "$BENCH/results/egress.log" >/dev/null 2>&1 &
    sleep 1
  fi
  code=$(docker run --rm "${RUN_NET[@]}" --entrypoint curl "$IMAGE" -s -o /dev/null -w '%{http_code}' --max-time 8 \
    -H "Authorization: Bearer $LOCAL_API_KEY" "$base_url/models" || true)
  [ "$code" = "200" ] || { echo "Model endpoint $base_url not reachable from the container (HTTP $code). Is the SSH tunnel up?" >&2; exit 3; }
fi

# Java and Maven take the home directory from /etc/passwd, not $HOME, so mount the run's home
# where the image's passwd entry for this uid points (uid 1000 is "node" in the image)
AGENT_HOME=$(docker run --rm --user "$(id -u):$(id -g)" --entrypoint sh "$IMAGE" -c 'getent passwd "$(id -u)" | cut -d: -f6')
AGENT_HOME="${AGENT_HOME:-/home/agent}"

# BENCH_RUN_SUFFIX keeps concurrent runs (bench/load.sh) that start in the same second apart
RUN="$RESULTS/$task/$harness-$target-${model//\//_}-$(date +%y%m%d_%H%M%S)${BENCH_RUN_SUFFIX:+-$BENCH_RUN_SUFFIX}"
mkdir -p "$RUN/home"; cp "$TASK/prompt.md" "$RUN/prompt.md"
# Refuse to start on a workspace whose .git hooks or config changed since the last run (see gitguard.py)
python3 "$BENCH/gitguard.py" check "$WS" --before || exit 3
python3 "$BENCH/reset.py" "$BASE_TAG" "$WS"
[ -x "$TASK/prepare.sh" ] && "$TASK/prepare.sh" "$WS"
# The agent gets a git folder with the base tag's history only, mounted over /work/.git (see agentgit.py)
mapfile -t git_mounts < <(python3 "$BENCH/agentgit.py" "$WS" "$BASE_TAG" "$RUN")
[ ${#git_mounts[@]} -gt 0 ] || { echo "agentgit.py made no git folder for $RUN" >&2; exit 3; }
# Run folders the task set mounts into the agent container (taskset.toml [agent] run_dirs, create)
run_mounts=()
for kv in "${TASKSET_RUN_DIRS[@]}"; do mkdir -p "$RUN/${kv#*=}"; run_mounts+=(-v "$RUN/${kv#*=}:${kv%%=*}"); done
for d in "${TASKSET_RUN_CREATE[@]}"; do mkdir -p "$RUN/$d"; done
# The chatgpt target borrows the ChatGPT login for this run; it is taken back (and kept if Codex refreshed it)
# however the run ends, so it never stays in the run folder
if [ "$target" = chatgpt ]; then
  python3 "$BENCH/chatgpt_auth.py" install "$CHATGPT_HOME" "$RUN/home/.codex" || exit 3
  trap 'python3 "$BENCH/chatgpt_auth.py" collect "$CHATGPT_HOME" "$RUN/home/.codex"' EXIT
fi
printf '[user]\n\tname = Bench Agent\n\temail = agent@bench.example\n' > "$RUN/home/.gitconfig"
registry_pid=""
if [ "${NEEDS_REGISTRY:-0}" = 1 ]; then
  mkdir -p "$RUN/home/.m2"; cp "$TASKSET/container/maven-settings.xml" "$RUN/home/.m2/settings.xml"
  python3 "$BENCH/maven-repo.py" --listen "$BRIDGE_IP:8081" --seed "$DEPS/maven-seed" \
    --store "$RUN/registry" --log "$RUN/registry.log" & registry_pid=$!
  sleep 1
fi
{ echo "task=$task"; echo "harness=$harness"; echo "target=$target"; echo "model=$model"
  echo "taskset=$TASKSET"; echo "base_tag=$BASE_TAG"; echo "workspace=$WS"; echo "start=$(date -Is)"
  for kv in ${BENCH_META:-}; do echo "$kv"; done; } > "$RUN/meta.txt"
[ "${BENCH_YOLO:-0}" = 1 ] && echo "yolo=1" >> "$RUN/meta.txt"
[ -n "${BENCH_VARIANT:-}" ] && echo "variant=$BENCH_VARIANT" >> "$RUN/meta.txt"

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
  --cap-drop ALL --security-opt no-new-privileges \
  -v "$BENCH/container/entrypoint.sh:/usr/local/bin/entrypoint.sh:ro" \
  --user "$(id -u):$(id -g)" -e HOME="$AGENT_HOME" -v "$RUN/home:$AGENT_HOME" \
  -v "$WS:/work" "${git_mounts[@]}" -v "$RUN/prompt.md:/run/prompt.md:ro" "${run_mounts[@]}" \
  -v "$REPO/opencode:/harness/opencode:ro" -v "$REPO/codex:/harness/codex:ro" \
  -e HARNESS="$harness" -e PROMPT_FILE=/run/prompt.md -e AGENT_WORKDIR="$TASKSET_AGENT_WORKDIR" \
  -e MODEL_REF="$profile/$model" -e PROFILE="$profile" -e PROVIDER="$provider" -e MODEL="$model" \
  -e LOCAL_BASE_URL="$base_url" -e LOCAL_API_KEY -e LOCAL_MODEL \
  "${api_key_env[@]}" -e OPENAI_MODEL -e BENCH_YOLO "${proxy_env[@]}" \
  "$IMAGE"; }
if [ ${#tty[@]} -eq 0 ]; then docker_run 2>&1 | tee "$RUN/console.log" || true; else docker_run || true; fi

echo "end=$(date -Is)" >> "$RUN/meta.txt"
[ "$target" = chatgpt ] && python3 "$BENCH/chatgpt_auth.py" collect "$CHATGPT_HOME" "$RUN/home/.codex"
[ -n "$registry_pid" ] && kill "$registry_pid" 2>/dev/null
guard() { python3 "$BENCH/gitguard.py" check "$WS" --run "$RUN" || echo "!! $1 changed the workspace's .git hooks or config; restored, evidence in $RUN/gitguard" >&2; }
guard "the agent"
# Grading runs the agent's build files and tests, in a throwaway container without network (sandbox.py)
python3 "$BENCH/grade.py" "$task" "$RUN/grade" "$WS" ${GRADE_WITH_NG:+--with-ng} || true
guard "grading"
python3 "$BENCH/score.py" "$RUN" || true
echo "Run saved in $RUN. Transcript: $RUN/home (OpenCode: .local/share/opencode, Codex: .codex/sessions)"
echo "Grade it: bench/review.py $RUN   (or later: bench/review.py next)"
