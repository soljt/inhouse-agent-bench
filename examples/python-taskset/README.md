# Example task set: textstats

A complete, small task set to try the benchmark with and to copy from when you write your own. The workspace
is a tiny Python package, `textstats`, with an `AGENTS.md` of house rules; the two tasks are graded with
pytest.

| Task | What it asks | What the grader checks |
| --- | --- | --- |
| `e1-word-count` | Fix `word_count` for double spaces, tabs and newlines | hidden pytest, existing tests, a test added, no new regex |
| `e2-slugify` | Add `slugify(title)` | hidden pytest, existing tests, a test added, reuses `normalize_whitespace` |

## Try it

```bash
docker build --network host -t llm-bench-base bench/container                          # framework base image
docker build --network host -t llm-bench-example examples/python-taskset/container    # + pytest
python3 examples/python-taskset/make_workspace.py                                      # ~/llm-bench-example

export BENCH_TASKSET=$PWD/examples/python-taskset       # or put it in .env
bench/run.sh cx local e1-word-count                     # needs a model target, see the main README
bench/review.py next
```

To check the graders without a model, apply a task's reference fix and grade it:

```bash
python3 bench/reset.py example-base
git -C ~/llm-bench-example apply $PWD/examples/python-taskset/tasks/e1-word-count/reference.patch
python3 bench/grade.py e1-word-count /tmp/e1/grade      # writes /tmp/e1/grade/auto.json
```

## How it is put together

```
taskset.toml        image, workspace, agent start folder, grader module
make_workspace.py   builds the workspace repo and tags its starting point (example-base)
container/          the image: FROM llm-bench-base, plus pytest
lib/example.py      Grader subclass with a pytest check; the repository's RULE_BREAKS and BUILD_KEYS
tasks/<id>/
  prompt.md         what the agent is asked
  task.env          BASE_TAG=example-base
  grade.py          grade(g) runs the checks; reuse(auto) maps one of them to the rubric
  hidden/           tests the agent never sees
  reference.patch   a good answer, for checking the grader
```

Everything the framework needs from a task set is in `bench/lib/taskset.py`; the checks a grader can call
are in `bench/lib/checks.py`, and the rubric defaults a task can override are in `bench/lib/rubric.py`.
