#!/usr/bin/env bash
# One-time setup on a dev VM. Pins harness versions so everyone runs the same thing.
set -euo pipefail
REPO="$(cd "$(dirname "$(readlink -f "$0")")/.." && pwd)"
OPENCODE_VERSION=1.18.34
CODEX_VERSION=0.162.1

node -e 'process.exit(+process.versions.node.split(".")[0] >= 20 ? 0 : 1)' \
  || { echo "Node 20+ required"; exit 1; }
npm i -g "opencode-ai@$OPENCODE_VERSION" "@openai/codex@$CODEX_VERSION"
[ -f "$REPO/.env" ] || cp "$REPO/.env.example" "$REPO/.env"
echo "Installed opencode $(opencode --version), $(codex --version). Now fill in $REPO/.env"
