# Base image for task sets: Node 20 with both agent harnesses, git, Python 3 and curl. A task set's image adds
# its own toolchain on top (FROM llm-bench-base); see examples/python-taskset/container/Dockerfile.
# Build: docker build --network host -t llm-bench-base bench/container
#
# What run.sh needs from any task set image: bash, git, python3, curl, opencode and codex on the PATH, and a
# user for your uid whose home is /home/agent (run.sh runs containers as your uid with HOME=/home/agent, and
# Java and Maven read the home from /etc/passwd, not $HOME). In node images uid 1000 is "node", renamed here.
FROM node:20-bookworm
RUN apt-get update \
 && apt-get install -y --no-install-recommends git python3 ripgrep less unzip curl \
 && rm -rf /var/lib/apt/lists/* \
 && usermod -l agent -d /home/agent node && groupmod -n agent node
# Pinned to the same versions as scripts/setup.sh
ARG OPENCODE_VERSION=1.18.34
ARG CODEX_VERSION=0.162.1
RUN npm i -g "opencode-ai@${OPENCODE_VERSION}" "@openai/codex@${CODEX_VERSION}" && npm cache clean --force
ENV OPENCODE_DISABLE_AUTOUPDATE=1 OPENCODE_DISABLE_SHARE=1 OPENCODE_DISABLE_MODELS_FETCH=1
# run.sh mounts the framework's entrypoint.sh here at run time
ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
