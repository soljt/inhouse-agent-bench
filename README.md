# inhouse-agent-bench

Benchmark coding agents on tasks from your own code base. Public benchmarks measure models on public code
they may have seen in training; this runs agent harnesses (OpenCode, Codex CLI) with a self-hosted or hosted
model on real tasks cut from your repository, in an isolated container, grades the result automatically, and
helps a human grade the rest blind. Use it to decide whether a self-hosted model is good enough for your
team, or which harness and model to adopt.

Two things live here:

1. **Launchers** (`bin/oc`, `bin/cx`) that run OpenCode and Codex CLI against your self-hosted model, or
   against OpenAI's API, with their settings locked down.
2. **A benchmark** (`bench/`) that runs those harnesses on real coding tasks in an isolated
   container, grades the result automatically, and helps a human grade the rest. The tasks come from a
   **task set**, a separate folder you write for your own code base (see Task sets); an example ships in
   `examples/python-taskset/`.

Shared config for running OpenCode and Codex CLI against your self-hosted model,
with an OpenAI target that exercises the same code path and gives a hosted model to compare against.

| Target | Endpoint | Used for |
| --- | --- | --- |
| `local` (default) | vLLM (or any OpenAI-compatible server) via SSH tunnel, `LOCAL_BASE_URL` | The real thing |
| `openai` (`dryrun` is its old name and still works) | `https://api.openai.com/v1`, model `OPENAI_MODEL` (or `DRYRUN_MODEL`) | A hosted model to compare against; testing config and tooling without a server |
| `chatgpt` (Codex only) | ChatGPT's Codex backend with a ChatGPT subscription login, model `CHATGPT_MODEL` | The same comparison, paid by a subscription instead of API credit |

OpenCode talks Chat Completions to the local server; Codex talks the Responses API (it no longer
supports Chat Completions for custom providers). vLLM serves both, so the smoke
test checks both. Against OpenAI both harnesses use Responses, and that is all the smoke test checks there.

The OpenCode `openai-api` provider uses OpenAI's own adapter (`@ai-sdk/openai`) rather
than the generic one used for vLLM: the generic adapter sends `max_tokens`, which
OpenAI's reasoning models reject. So an OpenCode run against OpenAI tests the harness, not
the vLLM adapter; the local smoke test covers that.

Both harnesses run OpenAI models at reasoning effort high and, like the local model, with a 131,072 token
context window (`codex/openai-api.config.toml`, the `openai-api` provider in `opencode/opencode.json`), so
scores compare on equal terms. The provider is called `openai-api` because both harnesses have a built-in
`openai` provider. Your code and the task set go to OpenAI on these runs.

### The chatgpt target: Codex on a ChatGPT subscription

Codex can sign in with a ChatGPT account instead of an API key; the `chatgpt` target runs the benchmark that
way, with the same reasoning effort and context window as `openai` (`codex/chatgpt.config.toml`). OpenCode is
not supported on it.

1. **Log in once**, with the account meant for the benchmark: `bin/cx chatgpt login --device-auth`. It prints a
   URL and a code to enter in a browser where that account is signed in. If ChatGPT refuses the device code,
   turn on device code login for Codex in the account's (or workspace's) security settings, or run
   `bin/cx chatgpt login` on a machine with a browser. A Codex personal access token works too, and suits
   unattended runs better since it is not refreshed: `printenv CODEX_ACCESS_TOKEN | bin/cx chatgpt login
   --with-access-token`. The login is kept in `.codex-chatgpt/auth.json`
   (`CHATGPT_HOME`, gitignored), apart from your personal `~/.codex`. `bin/cx chatgpt login status` checks it.
2. **Set `CHATGPT_MODEL`** in `.env`, then `bench/run.sh cx chatgpt <task>`, or
   `bench/suite.py --target chatgpt --harness cx`.

Each run borrows the login (`bench/chatgpt_auth.py`): it is copied into the run's Codex home, copied back if
Codex refreshed a sign-in (refresh tokens work only once), and deleted from the run folder however the run ends. So
runs of this target must not overlap, and `load.sh` refuses it. The agent can read the login while it runs;
its container reaches only `chatgpt.com` and `auth.openai.com`, through its own proxy port (3129). The API key
is not passed in. Codex also tries to fetch from `*.oaiusercontent.com`; that is denied and runs work without it.

Before pointing it at private code: a personal plan (Plus, Pro) may use conversations for training unless
that is turned off in the account's data controls; business plans don't. Runs count against the plan's usage
limits, so a long suite stops when they run out (a run cut off that way is graded as it stands); the report has
token counts but no cost for these runs.


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
| `bench/vllm_stats.py` | Throughput and KV cache stats from a vLLM server log |
| `bench/review.py`, `bench/report.py`, `bench/regrade.py` | Human review in the browser; comparison report; grading again |
| `bench/sandbox.py`, `bench/agentnet.py`, `bench/gitguard.py`, `bench/agentgit.py`, `bench/egress-proxy.py` | Isolation (see Security model) |
| `bench/results/` | Run folders and reports (gitignored) |
| `examples/python-taskset/` | A small complete task set to try and to copy |

## Setup

```bash
scripts/setup.sh          # installs pinned opencode + codex, creates .env
$EDITOR .env              # OPENAI_API_KEY (platform key), LOCAL_API_KEY, BENCH_TASKSET
```

## Use

```bash
scripts/smoke-test.sh openai      # one tool call on the Responses API; expect PASS
bin/oc openai                     # OpenCode TUI against OpenAI
bin/cx openai                     # Codex against OpenAI

ssh -N -L 8000:localhost:8000 root@<pod-ip> -p <port> -i ~/.ssh/<key>   # in another terminal
scripts/smoke-test.sh             # same checks against vLLM
bin/oc                            # OpenCode against your model
bin/cx                            # Codex against your model
```

Run them from inside the project you want the agent to work on, e.g.
`cd ~/git/some-repo && ~/inhouse-agent-bench/bin/oc openai`.

## What the launchers lock down

- **OpenCode:** only the two configured providers are enabled; sharing, auto-update and the
  models.dev catalogue fetch are off; the title-generation model is the local model.
  Edits, shell commands and web fetches prompt, except read-only git/ls; web search is denied.
- **Codex:** runs with its own `CODEX_HOME` (`.codex-home/`, gitignored), so your
  personal ChatGPT login, history and memories are never used. Analytics, update
  checks and OpenAI's hosted web search are off; workspace-write sandbox, approval on request.

These are settings in the software, not guarantees. Enforce no-egress at the
firewall or container level.

## Benchmark

### What a run does

`bench/run.sh <oc|cx> <local|openai|chatgpt> <task-id>`:

1. **Checks isolation.** The agent network must have no internet and no way to the host's own services; the
   model endpoint must answer through the gateway. The workspace's `.git` must match its baseline (gitguard).
2. **Resets the workspace** to the task's base tag and runs the task set's after-reset hook in the sandbox
   (`reset.py`; for example rebuilding generated files). Runs the task's `prepare.sh` if it has one. Gives the
   run its own git folder with only the base tag's history (`agentgit.py`), mounted over `/work/.git`.
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
- Your own task set, kept in its own (possibly private) repo: point `BENCH_TASKSET` at it. Its README
  describes its workspace tags, image and setup.

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
bench/suite.py --runs 3           # three graded runs per task and harness (for the finalists), breadth first
touch ~/suite.stop                # from another terminal: stop after the current run
```

The suite skips each task and harness that already has `--runs` (default 1) graded runs for the same target,
model, task set, `BENCH_VARIANT` and `BENCH_YOLO`, so stopping it (with `~/suite.stop`, or Ctrl+C: the unfinished run is not
graded and runs again) and starting it again carries on where it left off. Runs marked exclude in review
and load test runs don't count. A run over `--timeout-min` (45) is stopped, graded as it stands and marked
`timed_out`. When `run.sh` refuses to start a run (model endpoint down, isolation or gitguard problem), the
suite stops. Output also goes to `bench/results/suite-<yymmdd>.log`. With `--runs`, every task gets its
first run before any gets its second, so a suite stopped early still covers every task once. Raising
`--runs` later only adds the missing runs; the review page then shows all runs of a task together.

### Load tests

A load test measures how the setup copes with several agents at once, not how good their work is. It assumes
quality doesn't depend on load and measures the two separately, so its runs are graded automatically but
never reviewed, and never count as done for `suite.py`.

```bash
bench/load.sh oc local e1-word-count 1 2 4 8     # 1 agent, then 2, 4 and 8 at once (15 runs)
```

- Use a short task that is graded fully automatically. Tasks that need
  the Maven registry are refused, because the registry has a fixed port.
- Don't run a suite at the same time: both would compete for the GPUs.
- Each call makes a new group, `bench/results/load-<time>/` (launcher logs and `report.md`), and its runs are
  ordinary run folders named `...-L<level>w<slot>`, so nothing is overwritten. `bench/report.py` shows every
  group in its "Load tests" section (`--load <group>` for one).

The report is measured from the agents' side (their transcripts), one row per concurrency level:

| Column | Meaning |
| --- | --- |
| gates, auto /12 | Runs passing the gates; automatic subtotal (correctness, procedure, reuse, scope). A sanity check that load didn't break the work |
| wall min | Time per run, tool time included |
| s per request | Time per model request |
| out tok/s per agent | Output tokens per second per agent, model time only (prefill included) |
| aggregate out tok/s | All agents' output tokens over the level's wall clock: the server's useful throughput |

What it can't see is the server's side: KV cache use, requests queued for room, prefill throughput. For
that, summarise the vLLM log of the same time window. The log uses the pod's clock, which on RunPod is UTC
(check with `date` there): take the load test's start and end from its runs and convert them.

```bash
ssh root@<pod-ip> -p <port> -i ~/.ssh/<key> cat /root/vllm.log | bench/vllm_stats.py --since <start> --until <end>
```

`bench/report.html` shows the load tests as charts per concurrency level, every group in the same chart, so two
models (or two server setups) compare at a glance.

It prints the startup numbers (model memory and KV cache per GPU, maximum concurrency) and, from vLLM's
10-second stats lines, a table per number of running requests: generation throughput in total and per
request, prompt throughput, KV cache usage and the most requests waiting. Requests waiting means the server
is full (KV cache or `--max-num-seqs`): the concurrency where that starts is the setup's limit. Record both
tables with the model's server report in `results/`.

### Reviewing and reporting

```bash
bench/review.py next --blind    # grade task by task in the browser: all runs of a task side by side, ranked
bench/review.py next            # same, harness and model shown
bench/review.py task e2-slugify   # one task (again), e.g. to change a grade or a rank
bench/review.py pending         # list the runs that still need a score or a rank
bench/review.py <run>           # one run on its own, no ranking
bench/review.py <run> --pager   # terminal and $EDITOR instead of the browser (one run, no ranking)
bench/report.py                 # bench/results/report.html (open in a browser), report.md and report.csv
bench/regrade.py <run>...       # grade finished runs again with the current grader, from their saved diff
```

The review page is one page per task, with tabs:

- **Task**: the prompt (and follow-up turns), the rubric (what each dimension asks, what each score band looks
  like, the task's own notes) and the reference solution, folded away.
- **Tests & gates**: how each gate is decided for this task (the build keys its runs report, the behaviour
  rule, with the code when the task has its own), then the task's `grade.py`, everything in its `hidden/`
  and any other file it names in `GRADER_FILES`, verbatim.
- **Run A, B, ...**: one per run of the task, from every harness and model. The agent's final messages, gates
  and suggested scores, the test results (each test run the grader recorded, with every failed test named
  and its error, or the compile errors when the hidden tests did not compile; the header and the Grade tab
  name the failed tests when the behaviour gate failed), the automatic checks (`auto.json`, with each key's good value and what it measures),
  telemetry, the commands it ran, the diff side by side, the grader logs and any sections the task adds
  (T12: its findings). Each tab ends with that run's grade form. Blind, the letters are shuffled and harness
  and model hidden.
- **Grade & rank**: every run's scores side by side (the same fields as the run tabs, kept in sync), the
  running total, a gate override (below), and the ranking: order the runs from best to worst (drag, the arrows, or "Order by total"
  and then adjust). The page refuses to save until the runs are ranked.

"Save and next task" writes each run's `<run>/grade/human.json` and moves on. A run is pending when a
dimension has neither a human nor a suggested score, or when its task's runs were not all ranked together, so
a new run (the next model's suite) puts its whole task back in the queue: you grade the new runs and rank them
among the old ones, whose grades are filled in. Load test runs need no human score and never show up as
pending; `exclude` drops a run (aborted, broken) from review, the ranking and the report.

**Scoring.** Six dimensions, 1 to 10 each: correctness, procedure, reuse, scope (suggested automatically),
verification and merge-worthiness (human only). The total is their mean; a run that fails a gate (builds,
behaviour = hidden test, no harm) scores 0 and needs no scores, but is still ranked. A human score always
overrides the suggestion. Failure tags: bad tool call, loop, gave up, wrong fix, rule break, false claim.
**Gate overrides.** When a run fails the builds or behaviour gate but you judge the check wrong for that run
(say the agent solved it in a valid way the hidden test did not foresee), write the reason in "override
gate". The run then counts as passed and is scored; the failed check stays recorded, its Correctness has no
suggestion (you score it), and the report lists every override with its reason. A run that did harm cannot
be overridden. If the check is wrong for every run, fix the hidden test and `bench/regrade.py` the task's
runs instead, so past and future runs are judged alike.
The rank (`rank` of `rank_n` in `human.json`) is kept apart from the total: scores say how good a run is on
its own, the rank says which of the runs on the same task you would rather have. A `human.json` from the old
0 to 3 scale (without `"scale": 10`) is ignored, so its run is graded again.

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
   `behaviour(auto)`, `correctness(auto)`, `reuse(auto)`, `procedure(auto, score, broken)`. These work on a
   coarse 0 to 3 scale (`band` and `misses` help); the suggestion shown is mapped onto 1 to 10 (0, 1, 2, 3
   become 1, 4, 7, 10). `REGRADE_FROM_DIFF = False` when the result is not in the workspace diff, and
   `REGRADE_KEEP = [...]` for keys a regrade cannot reproduce from the diff (a check of untracked, generated
   files): `bench/regrade.py` takes those over from the previous `auto.json`.
6. Tell the reviewer what your checks mean, in the same `grade.py`. Without this the review page lists a key
   with no explanation and no colour:
   ```python
   # key -> (good value, what it measures). Good: True or False, 0 (any count above is bad),
   # 'high' or 'low' (more or less is better, no fixed line), or None (information only)
   AUTO_KEYS = {
       'new_regexes': (0, 'New re.sub/split/findall/compile calls in textstats/ (AGENTS.md: reuse text.py); feeds Reuse'),
   }
   # Task specific grading notes, shown under the dimension in the rubric
   RUBRIC_NOTES = {
       'reuse': 'str.split() or normalize_whitespace, not a new regex.',
   }
   ```
   Keys every task of your task set shares (a build gate, a repository rule) go in an `AUTO_KEYS` dict in
   the task set's module (`taskset.toml`: `module`) instead; the generic checks' keys are described in
   `rubric.AUTO_KEYS`. A `<key>_summary` needs no entry. `review.py` reads all three, the task's last.
7. If the grader uses test files from outside the task's `hidden/` (another task's hidden tests, a fixture in
   the task set's `lib/`), list them in `GRADER_FILES` (paths relative to the task folder), so the Tests &
   gates tab shows them next to `grade.py` and `hidden/`:
   `GRADER_FILES = ['../other-task/hidden/shared_fixture.py', '../../lib/fake_repository.py']`.
   The test results come from the logs of the test checks (`g.junit`, `g.command` with a summary, the task
   set's own): a key `K` with a `K_summary` in `auto.json` and a `K.log` in `grade/`. JUnit (RunTests),
   Jest `--verbose` and pytest output are understood (`bench/lib/testlogs.py`). If a log has another name,
   map it in `TEST_LOGS = {'hidden_test_ok': 'hidden-test.log'}`; if the behaviour gate is not
   `hidden_test_ok`, name its key in `BEHAVIOUR_TEST`.
   Start `grade.py` with a docstring that says in plain words how a run is graded (each gate, what the
   hidden tests check, how each suggestion is computed, what is left to the human): the Tests & gates tab
   shows the file, and the reviewer reads that before the code. The example task set's tasks have examples.
8. Optionally add sections to the review page with `review_sections()`, a list of
   `(title, kind, file in grade/[, options])`. Kinds: `md`, `text`, `findings` (one `path:line: text` finding
   per line, shown as a numbered list with the location set apart), `tsv` (tab separated rows; options:
   `columns` for the header, `finding_columns` for cells shown as findings, `ok` and `bad` for values to
   colour green or red).
9. Check the grader on the reference fix and on the base: `bench/reset.py <tag>`, apply the patch to the
   workspace, `bench/grade.py <id> /tmp/<id>/grade`.

### Security model

The agent can change anything in its workspace, so nothing from the workspace runs on the host:

- **Agent container:** runs as your uid with `--cap-drop ALL` and `no-new-privileges`, on the `bench-agents`
  network: `--internal` (no route out) and without a host address on it. Its only neighbour is
  `bench-gateway`, called `host.docker.internal` there, which relays exactly four ports to the host: the model
  forwarder, two allowlist HTTPS proxies (`api.openai.com:443` for the `openai` target; `chatgpt.com:443` and
  `auth.openai.com:443` for `chatgpt`) and a Maven registry for tasks
  that set `NEEDS_REGISTRY=1`. `run.sh` verifies before each run that github.com and the host's ssh port are
  unreachable.
- **Deps** (the task set's deps folder) are mounted read-only into every container.
- **Grading and the after-reset hook** run in `sandbox.py`: a throwaway container without network, with the
  workspace writable and everything else it needs read-only. The host only runs git and Python.
- **Host git** in the workspace runs with hooks and fsmonitor disabled; `gitguard.py` compares the workspace's
  `.git` hooks, config and tags with a baseline before and after each run, restores it and marks the run
  `git_tampered=1` if something changed. A moved or deleted tag counts (every run starts from one); a new
  tag does not.
- **The agent's git** is a folder in the run (`agentgit.py`) holding only the task's base tag and its history,
  mounted over `/work/.git`. The workspace's real `.git` holds every task's base and the task set's own
  branches; an agent reading it (`git log --all`) could find another task's answer or an earlier run's branches.
  Whatever the agent commits or branches stays in `<run>/agent-git`. Keep a task's answer out of its base's
  history: cut a slice before its fix, and put commits that contain fixes (a review task's base) on a side branch.

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
  | GLM-5.3-Flash FP8 | 4x RTX PRO 6000, one machine | ~400 GB | any |

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

`OK` only means the driver says the GPUs support direct copies, not that the data arrives. On 2026-10-14 a
2-GPU pod and a 4-GPU pod both showed `OK` everywhere, yet direct copies came back with the wrong contents
(on the 4-GPU pod, every copy from a lower to a higher numbered GPU), and vLLM hung at startup. Neither host
had `iommu=pt` in its kernel settings, so the IOMMU most likely blocks the copies; a rented pod can't change
that. So check before installing anything (the PyTorch template has torch already):

```bash
cat /proc/cmdline       # the host's kernel settings: iommu=pt is a good sign, nothing about iommu a warning
```

Then copy something between every pair of GPUs and compare:

```bash
python - <<'EOF'
import itertools, torch
x = torch.randn(64 * 1024 * 1024)                          # 256 MB
for a, b in itertools.permutations(range(torch.cuda.device_count()), 2):
    copied = x.to(f'cuda:{a}').to(f'cuda:{b}').cpu()
    print(f'GPU{a} -> GPU{b}:', 'OK' if torch.equal(copied, x) else 'WRONG DATA')
EOF
```

Expect `OK` for every pair. Any `WRONG DATA` (or a hang) means the host can't do direct copies, and what to do
depends on what the pod is for:

- **Load tests, or anything else that measures speed:** terminate the pod and deploy another (stopping and
  starting it may land on the same host). Without direct copies the GPUs exchange data through host memory,
  which is slower (a 4x RTX PRO 6000 GLM-5.3-Flash run read prompts 51 to 59% faster with them), and a server
  you own would have them, so speed measured without them understates what you'd get. If several redeploys
  fail, ask RunPod support for a host with working peer-to-peer copies.
- **Quality runs only (`bench/suite.py`):** the pod can stay, if NCCL works without direct copies. Check:

  ```bash
  cat > /root/nccl_test.py <<'PY'
  import torch, torch.distributed as dist, torch.multiprocessing as mp

  def run(rank, n):
      dist.init_process_group('nccl', init_method='tcp://127.0.0.1:29511', rank=rank, world_size=n)
      torch.cuda.set_device(rank)
      x = torch.full((64 * 1024 * 1024,), float(rank + 1), device='cuda')
      dist.all_reduce(x)
      torch.cuda.synchronize()
      print(f'rank {rank}:', 'OK' if bool((x == n * (n + 1) / 2).all()) else 'WRONG DATA', flush=True)
      dist.destroy_process_group()

  if __name__ == '__main__':
      n = torch.cuda.device_count()
      mp.spawn(run, args=(n,), nprocs=n)
  PY
  NCCL_P2P_DISABLE=1 timeout 120 python /root/nccl_test.py; echo "exit: $?"
  ```

  Every rank `OK` and `exit: 0`: start the server with `export NCCL_P2P_DISABLE=1` and
  `--disable-custom-all-reduce` (vLLM's own all-reduce copies directly and ignores the variable). vLLM sends
  data between the GPUs only through these two, so the answers are the same as on a host with direct copies;
  only the speed differs. Leave `BENCH_VARIANT` unset so the runs join the model's report row, and don't run
  load tests on this pod. (On the 4-GPU pod above, this test passed with `NCCL_P2P_DISABLE=1` and hung without.)

### Install vLLM (on every pod start, a few minutes)

```bash
export UV_CACHE_DIR=/root/.cache/uv XDG_CACHE_HOME=/root/.cache
pip install uv
uv venv /root/venv --python 3.12 && . /root/venv/bin/activate
uv pip install "vllm==0.30.0"     # pinned; Qwen3.8-27B needs >= 0.17.0, Flash-Next >= 0.30.0
# uv pip install "vllm==0.31.0"   # instead, for GLM-5.3-Flash (needs FlashInfer >= 0.6.18, see below)

# vLLM 0.30.0 ships PyTorch built for CUDA 13.0, but the template's nvcc is 12.8,
# which can't build FlashInfer's FP8 KV-cache kernels for the RTX PRO 6000 (sm_120).
# Install a matching CUDA 13 toolkit (~5 min). Skip if `nvcc --version` already says 13.x.
apt-get update && apt-get install -y cuda-toolkit-13-0
```

Flash-Next runs on stable vLLM 0.30.0 and later, but its [recipe](https://recipes.vllm.ai/Qwen/Qwen3.8-Flash-Next)
is tuned and tested on nightly builds; if the pinned version fails to load it, the recipe's Install section
generates the nightly install command.

GLM-5.3-Flash needs vLLM 0.31.0 (the version a published 4x RTX PRO 6000 run used) and FlashInfer 0.6.18 or
newer, for its sparse attention. Check with `python -c "import flashinfer; print(flashinfer.__version__)"`
after the install, and `uv pip install -U flashinfer-python` if it is older.

### Download weights (on every pod start, to the container disk)

Log in first (free account, read token from huggingface.co/settings/tokens) for higher rate limits. Type the
token into the SSH shell, never into the RunPod template's env vars, which the web console shows.

```bash
. /root/venv/bin/activate                  # the hf CLI comes with vLLM
hf auth login                              # paste the token; answer "n" to the git credential question
export HF_XET_HIGH_PERFORMANCE=1
hf download Qwen/Qwen3.8-27B-FP8 --local-dir /root/models/qwen3.8-27b-fp8
hf download Qwen/Qwen3.8-Flash-Next-FP8 --local-dir /root/models/qwen3.8-flash-next-fp8   # instead, for Flash-Next
hf download zai-org/GLM-5.3-Flash --local-dir /root/models/glm-5.3-flash-fp8               # instead, for GLM-5.3-Flash
```

Expect a few minutes even for big models: Qwen3.8-Flash-Next FP8 (186 GB) took about 3 min, peaking around
6 GB/s. An interrupted download resumes when you run the same command again.

| Model | Repo | Size | Serving |
| --- | --- | --- | --- |
| Qwen3.8-27B FP8 | `Qwen/Qwen3.8-27B-FP8` | 31 GB | 1x RTX PRO 6000, below |
| Qwen3.8-27B NVFP4 | `nvidia/Qwen3.8-27B-NVFP4` | 22 GB | 1x RTX PRO 6000 (untested) |
| Qwen3.8-Flash-Next FP8 | `Qwen/Qwen3.8-Flash-Next-FP8` | 186 GB | 2 or 4x RTX PRO 6000, below (untested) |
| GLM-5.3-Flash FP8 | `zai-org/GLM-5.3-Flash` | 328 GB | 4x RTX PRO 6000, below |
| GLM-5.3-Flash NVFP4 | `RedHatAI/GLM-5.3-Flash-NVFP4` | ~200 GB | 4x RTX PRO 6000, fallback if FP8 doesn't fit |

### Serve: common to every model

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
  --attention-config.indexer_kv_dtype fp8 \
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

### Serve: GLM-5.3-Flash on 4 GPUs

First served 2026-10-14 on 4x RTX PRO 6000 with vLLM 0.31.0. Set `LOCAL_MODEL=glm-5.3-flash` in `.env`
first (the smoke test fails with "model ... does not exist" when it doesn't match `--served-model-name`), and
install vLLM 0.31.0 (see Install vLLM).

```bash
export VLLM_ENGINE_READY_TIMEOUT_S=3600
vllm serve /root/models/glm-5.3-flash-fp8 \
  --served-model-name glm-5.3-flash \
  --host 127.0.0.1 --port 8000 \
  --tensor-parallel-size 4 \
  --max-model-len 131072 \
  --max-num-seqs 16 \
  --kv-cache-memory=8000000000 \
  --no-enable-flashinfer-autotune \
  --reasoning-parser glm47 \
  --enable-auto-tool-choice --tool-call-parser glm47 \
  --api-key "$VLLM_KEY" \
  2>&1 | tee /root/vllm.log
```

FP8 weights fit on 4 GPUs with a small KV cache. Per GPU, from the first serve (at
`--gpu-memory-utilization 0.95`, before `--kv-cache-memory`):

| Per GPU | GiB |
| --- | --- |
| Memory vLLM sees | 94.97 |
| Weights (`Model loading took`) | 76.91 |
| Weights plus memory outside PyTorch | 78.33 |
| Peak activations, measured while profiling | 6.32 |
| CUDA graphs | 0.09 |
| KV cache at `--gpu-memory-utilization 0.95` | 5.57 |
| KV cache that would fill the GPU completely (vLLM's own suggestion) | about 9.1 |

At 0.95 the KV cache held 679,191 tokens, `Maximum concurrency for 131,072 tokens per request: 5.18x`: about
35 KB per token across the four GPUs (8.8 KB on each), with a BF16 cache. `--kv-cache-memory` sets the KV
cache's size directly instead of as a share of the GPU: 8,000,000,000 bytes (7.45 GiB) gave 909,560 tokens,
6.94x, leaving about 1.7 GiB per GPU spare.

Why these settings:

- **No `--kv-cache-dtype fp8`:** it fails on this card with `pe_dim must be 64 for fp8_ds_mla`. vLLM's FP8
  layout for this kind of cache (MLA) expects a 64-number position part per token, which GLM-5.3-Flash
  doesn't have. The cache stays BF16, which is also slightly better quality.
- **`--kv-cache-memory=8000000000`** instead of `--gpu-memory-utilization`: the share left over after the
  weights and activations is small, so setting the KV cache's size directly gets a third more of it.
- **`--max-model-len 131072`** is the same context as the Qwen runs, so the runs compare.
- **`--max-num-seqs 16`** caps the batch, which shrinks the CUDA graphs and activation memory. Raise it if a
  load test runs more than 16 agents at once.
- **`--no-enable-flashinfer-autotune`**: FlashInfer's tuning step hung for 11 minutes on a 4x RTX PRO 6000 run.
- **`--reasoning-parser glm47`, `--tool-call-parser glm47`** are the vLLM recipe's. RedHatAI's NVFP4 model
  card uses `--reasoning-parser glm45`; try it if thinking text shows up in the answers.
- **`VLLM_ENGINE_READY_TIMEOUT_S=3600`**: profiling, compiling and warming up took about 4 minutes
  (`init engine ... took 247 s`), longer than some of vLLM's default waits.
- **MTP speculative decoding** is left off: its draft weights and cache would come out of the same small
  budget.

Expect, from the first serve: attention backend `FLASHINFER_MLA_SPARSE_SM120`, weights read in about 15 s from
the container disk (`Loading weights took`), and `GPU KV cache size: 909,560 tokens, Maximum concurrency for
131,072 tokens per request: 6.94x` with `--kv-cache-memory=8000000000` (5.18x at 0.95).

### Troubleshooting

- **GLM-5.3-Flash: out of memory at startup or under load:** lower `--kv-cache-memory` (6,000,000,000 is
  close to what 0.95 gave), or go back to `--gpu-memory-utilization 0.95`.
- **GLM-5.3-Flash: maximum concurrency below 4x:** lower `--max-model-len` to 100000 (shorter than the Qwen
  runs; give them `BENCH_VARIANT=ctx100k`), or serve the NVFP4 weights (`RedHatAI/GLM-5.3-Flash-NVFP4`, or
  `local-inference-lab/GLM-5.3-Flash-NVFP4` which a 4x RTX PRO 6000 run used) with `BENCH_VARIANT=nvfp4`.
- **GLM-5.3-Flash: `pe_dim must be 64 for fp8_ds_mla`:** remove `--kv-cache-dtype fp8` (see the serve
  section). Another startup error in sparse MLA can mean FlashInfer is older than 0.6.18; after that, try
  `--enforce-eager`.
- **Several GPUs: slow prompt processing, `NS` in `nvidia-smi topo -p2p r`:** the GPUs can't copy to each
  other directly, often because the host's IOMMU blocks it (fixed with the `iommu=pt` kernel parameter,
  which a rented pod can't change). It still works through host memory; a 4x RTX PRO 6000 GLM-5.3-Flash run
  read prompts 51 to 59% faster once direct copies worked. Redeploy if you want to compare speeds.
- **Flash-Next: `no kernel image is available for execution on the device`, or another startup error in a GPU
  kernel:** the risk of a card nobody validated it on (sm_120). Try in this order: `--moe-backend triton`,
  then `--enforce-eager` (no CUDA graphs: slower, but avoids graph capture), then a nightly vLLM build.
- **Flash-Next: out of memory while loading:** check the offload is on (add `--engram-config
  '{"cpu_offload":true}'`), lower `--max-model-len`, or use 4 GPUs.
- **Flash-Next: cache capacity error at startup:** keep `--max-num-seqs 256`.
- **Several GPUs: startup hangs, every GPU at 100% use but low power (about 100 of 600 W):** the GPUs are
  waiting for each other, not computing. The hang can come right after `vLLM is using nccl` (NCCL connecting)
  or later, at the first forward passes (vLLM's own all-reduce, `CUSTOM` in the `all-reduce backends` log
  line, which writes straight into the other GPU's memory and ignores `NCCL_P2P_DISABLE`). Run the copy test
  in Check the pod: `WRONG DATA` means redeploy, or for quality runs only, the NCCL test and the workaround
  there. If every pair is `OK` but `topo -p2p r` says `NS`, start again with `NCCL_P2P_DISABLE=1` and
  `--disable-custom-all-reduce`.
- **GPU memory still in use after stopping vLLM:** Ctrl+C can leave the workers running. List them with
  `nvidia-smi --query-compute-apps=pid,process_name,used_memory --format=csv` and `kill -9` each PID.
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
[vLLM recipe](https://recipes.vllm.ai/Qwen/Qwen3.8-Flash-Next) (as of 2026-10-13); GLM-5.3-Flash
[vLLM recipe](https://recipes.vllm.ai/zai-org/GLM-5.3-Flash),
[4x RTX PRO 6000 benchmark](https://github.com/commdata2338/glm-5.3-flash-4x-rtx-pro-6000-benchmark) and
[NVFP4 model card](https://huggingface.co/RedHatAI/GLM-5.3-Flash-NVFP4) (as of 2026-10-14).
