# Benchmark sandbox: Java 17 + ant (FMPP builds) + Maven, Node 20 (Angular 13), and both agent harnesses.
# Build: docker build -t llm-bench ~/llm-harness/bench/container
FROM node:20-bookworm
RUN apt-get update \
 && apt-get install -y --no-install-recommends openjdk-17-jdk-headless ant maven git python3 ripgrep less unzip \
 && rm -rf /var/lib/apt/lists/* \
 && mkdir -p /usr/share/tomcat9/lib \
 && usermod -l agent -d /home/agent node && groupmod -n agent node
# run.sh runs containers as the host uid (1000 here) with HOME=/home/agent. Java and Maven read
# the home from /etc/passwd, not $HOME, so uid 1000 must be a user whose home is /home/agent.
# Pinned to the same versions as scripts/setup.sh
ARG OPENCODE_VERSION=1.18.34
ARG CODEX_VERSION=0.147.0
RUN npm i -g "opencode-ai@${OPENCODE_VERSION}" "@openai/codex@${CODEX_VERSION}" && npm cache clean --force
ENV JAVA_HOME=/usr/lib/jvm/java-17-openjdk-amd64 \
    OPENCODE_DISABLE_AUTOUPDATE=1 OPENCODE_DISABLE_SHARE=1 OPENCODE_DISABLE_MODELS_FETCH=1
COPY entrypoint.sh /usr/local/bin/entrypoint.sh
ENTRYPOINT ["/usr/local/bin/entrypoint.sh"]
