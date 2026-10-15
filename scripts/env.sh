# Sourced by bin/oc, bin/cx, scripts/smoke-test.sh and bench/run.sh:  . scripts/env.sh <env-file>
# Loads the env file (if it exists) and fills in the model target settings.
#
# Targets: local (your OpenAI-compatible server, e.g. vLLM) and openai (OpenAI's API). local is local's
# old name and dryrun is openai's; both still work. OPENAI_MODEL picks the OpenAI model (DRYRUN_MODEL still
# works). The local server is set with LOCAL_BASE_URL, LOCAL_API_KEY and LOCAL_MODEL; the old
# LOCAL_* names still work too. The harness configs read the LOCAL_* names, so both are exported.
if [ -f "$1" ]; then set -a; . "$1"; set +a; fi
export LOCAL_BASE_URL="${LOCAL_BASE_URL:-${LOCAL_BASE_URL:-}}"
export LOCAL_API_KEY="${LOCAL_API_KEY:-${LOCAL_API_KEY:-}}"
export LOCAL_MODEL="${LOCAL_MODEL:-${LOCAL_MODEL:-}}"
export LOCAL_BASE_URL="$LOCAL_BASE_URL" LOCAL_API_KEY="$LOCAL_API_KEY" LOCAL_MODEL="$LOCAL_MODEL"
export OPENAI_MODEL="${OPENAI_MODEL:-${DRYRUN_MODEL:-}}"

# True for the names of the local target
is_local_target() { [ "$1" = local ] || [ "$1" = local ]; }
# True for the names of the OpenAI target
is_openai_target() { [ "$1" = openai ] || [ "$1" = dryrun ]; }
