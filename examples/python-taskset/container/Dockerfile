# The example task set's image: the framework's base image plus pytest.
# Build: docker build --network host -t llm-bench-example <this folder>   (needs llm-bench-base, see bench/container)
FROM llm-bench-base
RUN apt-get update && apt-get install -y --no-install-recommends python3-pytest && rm -rf /var/lib/apt/lists/*
