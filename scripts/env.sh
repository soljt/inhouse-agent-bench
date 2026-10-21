# Sourced by bin/oc, bin/cx, scripts/smoke-test.sh and bench/run.sh:  . scripts/env.sh <env-file>
# Loads the env file (if it exists) and fills in the model target settings.
#
# Targets: local (your OpenAI-compatible server, e.g. vLLM) and openai (OpenAI's API). dryrun is openai's
# old name and still works. OPENAI_MODEL picks the OpenAI model (DRYRUN_MODEL still
# works). Target chatgpt is Codex only, with a ChatGPT login kept in CHATGPT_HOME (.codex-chatgpt/ by default) and
# CHATGPT_MODEL as the model. The local server is set with LOCAL_BASE_URL, LOCAL_API_KEY and LOCAL_MODEL,
# exported for the harness configs.
if [ -f "$1" ]; then set -a; . "$1"; set +a; fi
export LOCAL_BASE_URL="${LOCAL_BASE_URL:-}" LOCAL_API_KEY="${LOCAL_API_KEY:-}" LOCAL_MODEL="${LOCAL_MODEL:-}"
export OPENAI_MODEL="${OPENAI_MODEL:-${DRYRUN_MODEL:-}}"
export CHATGPT_HOME="${CHATGPT_HOME:-$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)/.codex-chatgpt}"
export CHATGPT_MODEL="${CHATGPT_MODEL:-}"

# True for the names of the local target
is_local_target() { [ "$1" = local ]; }
# True for the names of the OpenAI target
is_openai_target() { [ "$1" = openai ] || [ "$1" = dryrun ]; }
