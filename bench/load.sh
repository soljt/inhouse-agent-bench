#!/usr/bin/env bash
# Concurrent load test: for each concurrency level, start that many runs of one task at once against the same
# model server, each in its own workspace worktree (~/llm-bench-load/wN), without a TUI. Levels run one after
# the other. Results land in bench/results/<task>/ as usual, tagged load_group/load_level/load_slot, and
# bench/report.py summarises them in its "Load tests" section.
# Usage: bench/load.sh <oc|cx> <local|dryrun> <task-id> <level> [<level> ...]
#   e.g. bench/load.sh oc local e1-word-count 1 2 4 8
set -euo pipefail
BENCH="$(cd "$(dirname "$(readlink -f "$0")")" && pwd)"
[ $# -ge 4 ] || { sed -n '2,7p' "$0"; exit 2; }
harness="$1"; target="$2"; task="$3"; shift 3; levels=("$@")
TASK="$BENCH/tasks/$task"; [ -d "$TASK" ] || { echo "no task $task" >&2; exit 2; }
. "$TASK/task.env"
[ "${NEEDS_REGISTRY:-0}" = 1 ] && { echo "$task needs the Maven registry on a fixed port; not usable for load tests" >&2; exit 2; }
MAIN="${BENCH_WORKSPACE:-$HOME/llm-bench}"; DEPS="${BENCH_DEPS:-$HOME/llm-bench-deps}"; LOAD="${BENCH_LOAD_DIR:-$HOME/llm-bench-load}"
STAGGER="${LOAD_STAGGER:-3}"
TIMEOUT_MIN="${LOAD_TIMEOUT_MIN:-45}"   # per run; a run over this is stopped, graded as it stands, and marked timed_out
RESULTS="${BENCH_RESULTS:-$BENCH/results}"

max=0; for l in "${levels[@]}"; do [ "$l" -gt "$max" ] && max=$l; done
mkdir -p "$LOAD"
for i in $(seq 1 "$max"); do
  w="$LOAD/w$i"
  if [ ! -e "$w/.git" ]; then
    git -C "$MAIN" worktree add -q --detach "$w" "$BASE_TAG"
    ln -sfn "$DEPS/angular/node_modules" "$w/the app/angular/node_modules"
    ln -sfn "$DEPS/TypeScript/node_modules" "$w/the app/TypeScript/node_modules"
  fi
done

group="load-$(date +%y%m%d_%H%M%S)"
logs="$RESULTS/$group"; mkdir -p "$logs"
echo "load group $group: $harness $target $task, levels ${levels[*]}; per-run timeout ${TIMEOUT_MIN} min"
echo "launcher logs: $logs; follow an agent: tail -f $RESULTS/$task/*-L<level>w<slot>/console.log"
for level in "${levels[@]}"; do
  echo "== level $level: starting $level runs ($(date +%T))"
  pids=()
  for i in $(seq 1 "$level"); do
    BENCH_WORKSPACE="$LOAD/w$i" BENCH_SCRIPTED=1 BENCH_RUN_SUFFIX="L${level}w$i" \
    BENCH_META="load_group=$group load_level=$level load_slot=$i" \
      "$BENCH/run.sh" "$harness" "$target" "$task" > "$logs/L$level-w$i.log" 2>&1 < /dev/null &
    pids+=($!)
    sleep "$STAGGER"   # lets the first run start the egress forwarder before the others check for it
  done
  # Wait, with a status line per minute and a per-run timeout
  t0=$(date +%s); declare -A stopped=()
  while :; do
    running=0; status=""
    for i in $(seq 1 "$level"); do
      p=${pids[$((i - 1))]}
      run_dir=$(ls -d "$RESULTS/$task/"*"-L${level}w$i" 2>/dev/null | tail -1 || true)
      if kill -0 "$p" 2>/dev/null; then
        running=$((running + 1))
        last=$(sed 's/\x1b\[[0-9;]*[a-zA-Z]//g' "$run_dir/console.log" 2>/dev/null | tr -d '\r' | grep -v '^\s*$' | tail -1 | cut -c1-60 || true)
        status+="  w$i: ${last:-starting}\n"
        if [ $(( $(date +%s) - t0 )) -gt $((TIMEOUT_MIN * 60)) ] && [ -z "${stopped[$i]:-}" ]; then
          echo "   w$i over ${TIMEOUT_MIN} min: stopping its container"
          docker stop -t 10 "bench-$task-$p" > /dev/null 2>&1 || true
          [ -n "$run_dir" ] && echo "timed_out=1" >> "$run_dir/meta.txt"
          stopped[$i]=1
        fi
      fi
    done
    [ "$running" -eq 0 ] && break
    printf '   %s  %d/%d running, %d min\n%b' "$(date +%T)" "$running" "$level" $(( ($(date +%s) - t0) / 60 )) "$status"
    sleep 60
  done
  fails=0; for p in "${pids[@]}"; do wait "$p" || fails=$((fails + 1)); done
  echo "== level $level done ($(date +%T)), $fails launcher errors"
done
python3 "$BENCH/report.py" --load "$group" --out "$logs"
