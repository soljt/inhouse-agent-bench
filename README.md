# llm-harness

Two things live here:

1. **Launchers** (`bin/oc`, `bin/cx`) that run OpenCode and Codex CLI against our self-hosted model, or
   against OpenAI for a dry run, with their settings locked down.
2. **A benchmark** (`bench/`) that runs those harnesses on real coding tasks in an isolated
   container, grades the result automatically, and helps a human grade the rest. The tasks come from a
   **task set**, a separate folder you write for your own code base (see Task sets); an example ships in
   `examples/python-taskset/`.

Shared config for running OpenCode and Codex CLI against our self-hosted model,
with an OpenAI dry-run target that exercises the same code path.

| Target | Endpoint | Used for |
| --- | --- | --- |
| `local` (default) | vLLM (or any OpenAI-compatible server) via SSH tunnel, `LOCAL_BASE_URL` | The real thing |
| `dryrun` | `https://api.openai.com/v1` | Testing config, permissions and tooling before the server exists |

OpenCode talks Chat Completions; Codex talks the Responses API (it no longer
supports Chat Completions for custom providers). vLLM serves both, so the smoke
test checks both.

The OpenCode `dryrun` provider uses OpenAI's own adapter (`@ai-sdk/openai`) rather
than the generic one used for vLLM: the generic adapter sends `max_tokens`, which
OpenAI's reasoning models reject. So an OpenCode dry run tests the harness, not
the vLLM adapter; the smoke test covers that.


## Repository layout

| Path | What it is |
| --- | --- |
| `bin/oc`, `bin/cx` | Launchers for interactive use |
| `opencode/`, `codex/` | Harness configs, shared by the launchers and the benchmark container |
| `scripts/` | `setup.sh` (install pinned harnesses), `smoke-test.sh` (tool call on both APIs), `env.sh` (loads `.env`, target names) |
| `results/` | Model server reports (vLLM startup numbers) |
| `bench/run.sh`, `bench/suite.py`, `bench/load.sh` | One benchmark run; every task, resumable; a concurrency load test |
| `bench/container/` | The base image (Node 20, both harnesses, git, Python) task set images build on, and the entrypoint |
| `bench/reset.py` | Resets the workspace to a task's base tag and runs the task set's after-reset hook |
| `bench/grade.py`, `bench/lib/checks.py` | Grading runner and generic checks |
| `bench/score.py`, `bench/lib/rubric.py` | Gates and score suggestions from a run's checks |
| `bench/lib/taskset.py` | Finds and reads the task set: the contract between the framework and a task set |
| `bench/review.py`, `bench/report.py`, `bench/regrade.py` | Human review in the browser; comparison report; grading again |
| `bench/sandbox.py`, `bench/agentnet.py`, `bench/gitguard.py`, `bench/egress-proxy.py` | Isolation (see Security model) |
| `bench/results/` | Run folders and reports (gitignored) |
| `examples/python-taskset/` | A small complete task set to try and to copy |

## Setup

```bash
scripts/setup.sh          # installs pinned opencode + codex, creates .env
$EDITOR .env              # OPENAI_API_KEY (platform key), LOCAL_API_KEY, BENCH_TASKSET
```

## Use

```bash
scripts/smoke-test.sh dryrun      # one tool call on each API; expect PASS twice
bin/oc dryrun                     # OpenCode TUI against OpenAI
bin/cx dryrun                     # Codex against OpenAI

ssh -N -L 8000:localhost:8000 root@<pod-ip> -p <port> -i ~/.ssh/<key>   # in another terminal
scripts/smoke-test.sh             # same checks against vLLM
bin/oc                            # OpenCode against our model
bin/cx                            # Codex against our model
```

Run them from inside the project you want the agent to work on, e.g.
`cd ~/git/some-repo && ~/llm-harness/bin/oc dryrun`.

## What the launchers lock down

- **OpenCode:** only our two providers are enabled; sharing, auto-update and the
  models.dev catalogue fetch are off; the title-generation model is our own model.
  Edits, shell commands and web fetches prompt, except read-only git/ls; web search is denied.
- **Codex:** runs with its own `CODEX_HOME` (`.codex-home/`, gitignored), so your
  personal ChatGPT login, history and memories are never used. Analytics, update
  checks and OpenAI's hosted web search are off; workspace-write sandbox, approval on request.

These are settings in the software, not guarantees. Enforce no-egress at the
firewall or container level.

## Benchmark

### What a run does

`bench/run.sh <oc|cx> <local|dryrun> <task-id>`:

1. **Checks isolation.** The agent network must have no internet and no way to the host's own services; the
   model endpoint must answer through the gateway. The workspace's `.git` must match its baseline (gitguard).
2. **Resets the workspace** to the task's base tag and runs the task set's after-reset hook in the sandbox
   (`reset.py`; for example rebuilding generated files). Runs the task's `prepare.sh` if it has one.
3. **Runs the agent** in the task set's image with the task prompt, as a TUI or, for scripted tasks,
   unattended (follow-up turns from `turns/`).
4. **Checks `.git` again**, then **grades** the workspace in a throwaway sandbox (`grade.py` with the task's
   `grade.py`): diff, builds, hidden tests, rule checks. Results go to `<run>/grade/auto.json`.
5. **Scores** it (`score.py`): gates (builds, behaviour, no harm) and automatic suggestions for the scores
   that need no judgement.

Each run gets a folder `bench/results/<task>/<harness>-<target>-<model>-<time>/` with `meta.txt` (including
which task set it used), the prompt, the agent's home (its transcript), `console.log` for scripted runs, and
`grade/`.

### Task sets

The framework knows nothing about your code base; a task set brings it. It is a folder, found through
`BENCH_TASKSET` (environment or `.env`), with:

```
taskset.toml        image, workspace (git repo with the tasks' base tags), deps folder (optional, read-only
                    in containers), where the agent starts, grader module, reset and run folder settings
tasks/<id>/         prompt.md, task.env (BASE_TAG=<tag>), grade.py, and optionally hidden/ (tests the
                    agent never sees), turns/ (follow-up prompts: makes the task scripted), prepare.sh
lib/<module>.py     optional: a Grader subclass with checks for your code base, your rules (RULE_BREAKS)
                    and build keys (BUILD_KEYS)
hooks/, container/  whatever its after-reset hook and image need
```

`bench/lib/taskset.py` documents every key. Task sets in use:

- `examples/python-taskset/`: a tiny Python package with two pytest-graded tasks. Start here: its README
  shows how to build it, run it, and check its graders without a model.
- `~/my-taskset` (private repo): 14 tasks on slices of the app and the app's backend, with their
  workspace tags, image and setup in its README.

### Prerequisites

- Docker, the base image `docker build --network host -t llm-bench-base bench/container`, and the task set's
  own image (see its README).
- `.env` filled in as for the launchers (see Setup), with `BENCH_TASKSET`; for `local` the SSH tunnel must be up.
- The task set's workspace and anything its README asks for (for example a deps install).

### Running

```bash
bench/run.sh cx local e1-word-count                    # one run, TUI
BENCH_YOLO=1 bench/run.sh oc local e2-slugify          # TUI with every permission auto-approved
BENCH_SCRIPTED=1 bench/run.sh cx local e1-word-count       # no TUI, unattended
bench/load.sh oc local e1-word-count 1 2 4 8           # 1, then 2, 4, 8 agents at once
```

`load.sh` runs each concurrency level in its own worktree (`~/llm-bench-load/wN`), unattended, stops runs over
`LOAD_TIMEOUT_MIN` (45), and writes a report next to its launcher logs in `bench/results/load-<time>/`.

| Variable | Effect |
| --- | --- |
| `BENCH_TASKSET` | The task set folder (environment or `.env`) |
| `BENCH_YOLO=1` | Interactive harness auto-approves everything (Codex also skips its own sandbox); recorded as `yolo=1` |
| `BENCH_SCRIPTED=1` | Run a single prompt task without a TUI |
| `BENCH_VARIANT=<label>` | Free label for anything else that differs (vLLM settings, harness version); the report keeps it in its own row |
| `GRADE_WITH_NG=1` | Passed to graders as `with_ng` (a task set's GUI tasks: also run the Angular build, about 1 GB disk, 3 min) |
| `BENCH_WORKSPACE`, `BENCH_DEPS`, `BENCH_RESULTS`, `BENCH_ENV` | Other workspace, deps folder, results folder, env file |
| `LOAD_STAGGER`, `LOAD_TIMEOUT_MIN`, `BENCH_LOAD_DIR` | load.sh: seconds between starts, per run timeout, worktree folder |

### Running a whole suite

```bash
bench/suite.py --dry-run          # what would run for the model in .env, and what is already done
bench/suite.py                    # every task with oc and cx, unattended, one after the other
bench/suite.py --harness cx e1-word-count e2-slugify    # some tasks, one harness
touch ~/suite.stop                # from another terminal: stop after the current run
```

The suite skips each task and harness that already has a graded run for the same target, model, task set,
`BENCH_VARIANT` and `BENCH_YOLO`, so stopping it (with `~/suite.stop`, or Ctrl+C: the unfinished run is not
graded and runs again) and starting it again carries on where it left off. Runs marked exclude in review
and load test runs don't count. A run over `--timeout-min` (45) is stopped, graded as it stands and marked
`timed_out`. When `run.sh` refuses to start a run (model endpoint down, isolation or gitguard problem), the
suite stops. Output also goes to `bench/results/suite-<yymmdd>.log`.

### Reviewing and reporting

```bash
bench/review.py next            # review pending runs one by one in the browser, grade form at the bottom
bench/review.py next --blind    # same, harness and model hidden, random order
bench/review.py pending         # list them
bench/review.py <run> --pager   # terminal and $EDITOR instead of the browser
bench/report.py                 # bench/results/report.md and report.csv
bench/regrade.py <run>...       # grade finished runs again with the current grader, from their saved diff
```

The review page shows the prompt, the agent's final messages, the automatic checks, telemetry, the commands it
ran, the diff side by side, the grader logs and any sections the task adds. The form writes
`<run>/grade/human.json`. Load test runs need no human score and never show up as pending; `exclude` drops a
run (aborted, broken) from review and the report.

**Scoring.** Six dimensions, 0 to 3 each, total 18: correctness, procedure, reuse, scope (suggested
automatically), verification and merge-worthiness (human only). A run that fails a gate (builds, behaviour =
hidden test, no harm) scores 0. A human score always overrides the suggestion. Failure tags: bad tool call,
loop, gave up, wrong fix, rule break, false claim.

**The report** has one row per harness, target, model, and `BENCH_VARIANT`/`yolo` when set, with totals per
task, mean scores per dimension, cost (wall time, tokens, requests) and failure tags, plus a section per load
test. `report.csv` has one row per run.

### Writing a task

1. Build the starting state in the task set's workspace repo and tag it (or reuse a tag).
2. Create `tasks/<id>/` in the task set with `prompt.md` and `task.env` (`BASE_TAG=<tag>`).
3. Write hidden tests in `hidden/` that pass on a good fix and fail on the base, and a `reference.patch`.
4. Write `grade.py` with one function `grade(g)`, calling checks on `g` (`bench/lib/checks.py`, plus your task
   set's Grader subclass). From the example task set:
   ```python
   def grade(g):
       g.record_diff(); g.diff_check()
       g.scope_check(r'^(textstats|tests)/')
       g.pytest('tests_ok', ['tests'])
       g.pytest('hidden_test_ok', [g.hidden('test_word_count_hidden.py')])
       g.wrote_tests('tests/')
   ```
   A check other task sets could use goes into `checks.py`; one only your code base needs goes into your task
   set's `lib/`.
5. If the defaults don't fit, say in the same `grade.py` how its keys map to the rubric. The defaults
   (`bench/lib/rubric.py`): the behaviour gate is `hidden_test_ok`, the build gate is every build key present,
   correctness is 3 when the hidden test passes, procedure is 3 minus broken rules, scope comes from
   `out_of_scope_files`, and there is no reuse suggestion. Optional functions:
   `behaviour(auto)`, `correctness(auto)`, `reuse(auto)`, `procedure(auto, score, broken)`; also
   `review_sections()` for extra sections on the review page and `REGRADE_FROM_DIFF = False` when the
   result is not in the workspace diff.
6. Check the grader on the reference fix and on the base: `bench/reset.py <tag>`, apply the patch to the
   workspace, `bench/grade.py <id> /tmp/<id>/grade`.

### Security model

The agent can change anything in its workspace, so nothing from the workspace runs on the host:

- **Agent container:** runs as your uid with `--cap-drop ALL` and `no-new-privileges`, on the `bench-agents`
  network: `--internal` (no route out) and without a host address on it. Its only neighbour is
  `bench-gateway`, called `host.docker.internal` there, which relays exactly three ports to the host: the model
  forwarder, the allowlist HTTPS proxy (only `api.openai.com:443`, for dry runs) and a Maven registry for tasks
  that set `NEEDS_REGISTRY=1`. `run.sh` verifies before each run that github.com and the host's ssh port are
  unreachable.
- **Deps** (the task set's deps folder) are mounted read-only into every container.
- **Grading and the after-reset hook** run in `sandbox.py`: a throwaway container without network, with the
  workspace writable and everything else it needs read-only. The host only runs git and Python.
- **Host git** in the workspace runs with hooks and fsmonitor disabled; `gitguard.py` compares the workspace's
  `.git` hooks, config and tags with a baseline before and after each run, restores it and marks the run
  `git_tampered=1` if something changed. A moved or deleted tag counts (every run starts from one); a new
  tag does not.

What is left: the containers share the host kernel (keep it patched), and the model forwarder and proxy see
whatever the agent sends through them.

## Running the model on RunPod

### Pod

- **Template:** RunPod PyTorch (the CUDA 12.8 one works with the extra step below),
  not the vLLM template. The vLLM template auto-starts a default gated model
  (crash-loops without an HF token), only offers `vllm-latest`, and runs no sshd,
  so SSH port forwarding doesn't work.
- Secure Cloud, EU data center, sized per model:

  | Model | GPUs | Container disk | Host RAM |
  | --- | --- | --- | --- |
  | Qwen3.8-27B FP8 | 1x RTX PRO 6000 | ~80 GB | any |
  | Qwen3.8-Flash-Next FP8 | 2x RTX PRO 6000 (tight) or 4x (comfortable), one machine | ~220 GB | 128 GB or more: its 51 GB lookup table lives in RAM |

- **No volume.** Everything, weights included, lives on the container disk: downloading straight from
  Hugging Face to it is fast, so there is nothing worth keeping between pods. The container disk is wiped
  whenever the pod stops or restarts, including when you change its disk size, so every start begins with
  the steps below.
- Expose TCP 22 only. Never expose port 8000 over HTTP: the HTTPS proxy terminates
  TLS on RunPod's side.
- Connect with "SSH over exposed TCP" (`root@<ip> -p <port>`), not `ssh.runpod.io`,
  which doesn't support port forwarding. The port changes when the pod restarts.
- Stop or terminate the pod when done; either way nothing on it survives.

### Check the pod (every start)

```bash
nvidia-smi --query-gpu=index,name,memory.total --format=csv
free -g | head -2
df -h /root
```

Expect one line per GPU you ordered, each `NVIDIA RTX PRO 6000 Blackwell Server Edition, 97887 MiB`, and RAM
and disk as in the table above.

With several GPUs, also check how they are connected. The model is split across them, and they exchange
partial results after every layer; RTX PRO 6000s have no NVLink, so that goes over PCIe:

```bash
nvidia-smi topo -m          # the entry between each pair of GPUs
nvidia-smi topo -p2p r      # whether they can copy to each other directly
```

`topo -m` prints a matrix like this one (2 GPUs on one PCIe switch):

```
        GPU0    GPU1    CPU Affinity    NUMA Affinity
GPU0     X      PIX     0-47            0
GPU1    PIX      X      0-47            0
```

| Entry | Means | Verdict |
| --- | --- | --- |
| `PIX`, `PXB` | Same PCIe switch | Best |
| `NODE` | Same CPU, different PCIe root ports | Fine |
| `SYS` | Different CPU sockets: traffic crosses between them | Slow: redeploy for another machine |

`topo -p2p r` should show `OK` between every pair. `NS` (not supported) still works, but the GPUs then copy
through host memory, which is slower; see Troubleshooting if startup hangs.

### Install vLLM (on every pod start, a few minutes)

```bash
export UV_CACHE_DIR=/root/.cache/uv XDG_CACHE_HOME=/root/.cache
pip install uv
uv venv /root/venv --python 3.12 && . /root/venv/bin/activate
uv pip install "vllm==0.30.0"     # pinned; Qwen3.8-27B needs >= 0.17.0, Flash-Next >= 0.30.0

# vLLM 0.30.0 ships PyTorch built for CUDA 13.0, but the template's nvcc is 12.8,
# which can't build FlashInfer's FP8 KV-cache kernels for the RTX PRO 6000 (sm_120).
# Install a matching CUDA 13 toolkit (~5 min). Skip if `nvcc --version` already says 13.x.
apt-get update && apt-get install -y cuda-toolkit-13-0
```

Flash-Next runs on stable vLLM 0.30.0 and later, but its [recipe](https://recipes.vllm.ai/Qwen/Qwen3.8-Flash-Next)
is tuned and tested on nightly builds; if the pinned version fails to load it, the recipe's Install section
generates the nightly install command.

### Download weights (on every pod start, to the container disk)

Log in first (free account, read token from huggingface.co/settings/tokens) for higher rate limits. Type the
token into the SSH shell, never into the RunPod template's env vars, which the web console shows.

```bash
. /root/venv/bin/activate                  # the hf CLI comes with vLLM
hf auth login                              # paste the token; answer "n" to the git credential question
export HF_XET_HIGH_PERFORMANCE=1
hf download Qwen/Qwen3.8-27B-FP8 --local-dir /root/models/qwen3.8-27b-fp8
hf download Qwen/Qwen3.8-Flash-Next-FP8 --local-dir /root/models/qwen3.8-flash-next-fp8   # instead, for Flash-Next
```

Expect a few minutes even for big models: Qwen3.8-Flash-Next FP8 (186 GB) took about 3 min, peaking around
6 GB/s. An interrupted download resumes when you run the same command again.

| Model | Repo | Size | Serving |
| --- | --- | --- | --- |
| Qwen3.8-27B FP8 | `Qwen/Qwen3.8-27B-FP8` | 31 GB | 1x RTX PRO 6000, below |
| Qwen3.8-27B NVFP4 | `nvidia/Qwen3.8-27B-NVFP4` | 22 GB | 1x RTX PRO 6000 (untested) |
| Qwen3.8-Flash-Next FP8 | `Qwen/Qwen3.8-Flash-Next-FP8` | 186 GB | 2 or 4x RTX PRO 6000, below (untested) |

### Serve: common to both models

`--served-model-name` must match `LOCAL_MODEL` in `.env`, and `VLLM_KEY` must match
`LOCAL_API_KEY` (generate with `openssl rand -hex 32`). Set the key in the SSH
shell, not in the RunPod template's env vars. The model name also goes into every run folder's name, so each
model's runs and report rows stay apart. Start the server in tmux, after this in the same shell:

```bash
. /root/venv/bin/activate
export VLLM_CACHE_ROOT=/root/.cache/vllm VLLM_NO_USAGE_STATS=1 HF_HUB_OFFLINE=1
export CUDA_HOME=/usr/local/cuda-13.0 PATH=/usr/local/cuda-13.0/bin:$PATH
export VLLM_KEY=<your key>
```

It's ready at `Application startup complete`. Then pull the numbers worth recording out of the log:

```bash
grep -E 'Available KV cache memory|GPU KV cache size|Maximum concurrency|Model loading took|Loading weights took|[Aa]ttention backend|Using .* backend|startup complete' /root/vllm.log | grep -v Initializing | cut -c1-200
```

Then, on your machine: the SSH tunnel (see Use), `scripts/smoke-test.sh` (expect PASS twice) and
`bench/suite.py --dry-run` to see what a suite would run for this model.

### Serve: Qwen3.8-27B on 1 GPU

```bash
vllm serve /root/models/qwen3.8-27b-fp8 \
  --served-model-name qwen3.8-27b \
  --host 127.0.0.1 --port 8000 \
  --tensor-parallel-size 1 \
  --max-model-len 131072 \
  --gpu-memory-utilization 0.92 \
  --kv-cache-dtype fp8 \
  --reasoning-parser qwen3 \
  --enable-auto-tool-choice --tool-call-parser qwen3_xml \
  --api-key "$VLLM_KEY" \
  2>&1 | tee /root/vllm.log
```

Expect, from the first serve (`results/2026-10-07_qwen3.8-27b-fp8_1x-rtx-pro-6000.md`): model loading
28.5 GiB, KV cache 54.2 GiB or 1,648,594 tokens, maximum concurrency 12.6x at 131,072 tokens, attention backend
FlashInfer, about 5 min from start to ready (loading from the old volume; local disk is quicker).

- If the smoke test fails on `/chat/completions`, try `--tool-call-parser qwen3_coder`.
  If only `/responses` fails, that's vLLM's Responses API: OpenCode works, Codex won't.
- Record load time, GPU KV cache size (4 agents x 131k needs
  ~524k tokens) and max concurrency at 131k (want >= 4x).
- During agent runs, `Prefix cache hit rate` in the log should climb on later turns.

### Serve: Qwen3.8-Flash-Next on 2 or 4 GPUs (untested on RTX PRO 6000)

Set `LOCAL_MODEL=qwen3.8-flash-next` in `.env` first. On a 4-GPU pod use `--tensor-parallel-size 4`.

```bash
vllm serve /root/models/qwen3.8-flash-next-fp8 \
  --served-model-name qwen3.8-flash-next \
  --host 127.0.0.1 --port 8000 \
  --tensor-parallel-size 2 \
  --enable-expert-parallel \
  --max-model-len 131072 \
  --max-num-seqs 256 \
  --gpu-memory-utilization 0.90 \
  --kv-cache-dtype fp8 --attention-config.indexer_kv_dtype fp8 \
  --limit-mm-per-prompt '{"image":0,"video":0}' \
  --reasoning-parser qwen3 \
  --enable-auto-tool-choice --tool-call-parser qwen3_coder \
  --api-key "$VLLM_KEY" \
  2>&1 | tee /root/vllm.log
```

Why these settings, from the recipe:

- **Expert parallelism** (`--enable-expert-parallel`): with tensor parallelism, this is the recipe's recommended
  setup and faster than tensor parallelism alone. Pipeline parallelism is not supported.
- **The lookup table stays in host RAM:** vLLM offloads it by default (`--engram-config '{"cpu_offload":true}'`
  says so explicitly).
- **`--max-num-seqs 256`** avoids a cache capacity error at startup.
- **`--limit-mm-per-prompt`** turns off image and video input; the agents only send text.
- **131,072 context** is the same as the 27B, so the runs compare; the model's native maximum is 262,144.
- **MTP speculative decoding** is left off until the plain setup works. Then add
  `--speculative-config '{"method":"mtp","num_speculative_tokens":3}'`, which costs memory, and give those
  runs `BENCH_VARIANT=mtp3`.

Expect (estimates, nobody has run this here yet):

- The startup log lists 2 (or 4) workers, one per GPU, and loads 186 GB from local disk.
- Weights take about 63 GiB per GPU at TP2 or 32 GiB at TP4 (134 GB without the lookup table, split evenly).
- At TP2 that leaves about 20 GiB per GPU for the KV cache, activations and CUDA graphs. Four agents at
  131k tokens need about 7 GB of KV cache in total (12 of 48 layers keep one, ~14 KB per token at FP8).
- Maximum concurrency at 131,072 tokens should be 4x or more.
- Several minutes from start to ready.

### Troubleshooting

- **Flash-Next: `no kernel image is available for execution on the device`, or another startup error in a GPU
  kernel:** the risk of a card nobody validated it on (sm_120). Try in this order: `--moe-backend triton`,
  then `--enforce-eager` (no CUDA graphs: slower, but avoids graph capture), then a nightly vLLM build.
- **Flash-Next: out of memory while loading:** check the offload is on (add `--engram-config
  '{"cpu_offload":true}'`), lower `--max-model-len`, or use 4 GPUs.
- **Flash-Next: cache capacity error at startup:** keep `--max-num-seqs 256`.
- **Several GPUs: startup hangs while the workers connect:** often missing direct GPU copies (`NS` in
  `nvidia-smi topo -p2p r`). Start again with `NCCL_P2P_DISABLE=1` in the environment.
- **SSH drops immediately with "container ... is not running":** the container is
  crash-looping (usually the start command). Stop the pod and check its logs.
- **`RuntimeError: No supported CUDA architectures found for major versions [9, 10, 12]`:**
  FlashInfer is compiling with the template's CUDA 12.8 `nvcc`. Install the CUDA 13
  toolkit and set `CUDA_HOME`/`PATH` as above, in the same shell that starts vLLM.
- **`channel N: open failed: connect failed: Connection refused` in the tunnel:**
  something used the tunnel before vLLM was listening. Wait for startup; the tunnel is fine.
- **OpenCode hangs silently:** the tunnel is probably down; it retries without
  printing an error.

Sources: Qwen3.8-27B [model card](https://huggingface.co/Qwen/Qwen3.8-27B) and
[vLLM recipe](https://recipes.vllm.ai/Qwen/Qwen3.8-27B); Qwen3.8-Flash-Next
[model card](https://huggingface.co/Qwen/Qwen3.8-Flash-Next-FP8) and
[vLLM recipe](https://recipes.vllm.ai/Qwen/Qwen3.8-Flash-Next) (as of 2026-10-13).
