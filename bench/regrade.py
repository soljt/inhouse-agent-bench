#!/usr/bin/env python3
"""Grade finished runs again with the current grader, from the diff each run saved.

Usage: bench/regrade.py <run-dir> [<run-dir> ...] [--workspace <dir>]

For each run: reset the workspace to the task's base tag, apply the run's grade/diff.patch, grade, and
rescore. The previous auto.json is kept as grade/auto.prev.json; human.json is left alone.
The workspace (default ~/llm-bench) is reset, so it refuses to run while a benchmark container is up.
T2 cannot be regraded this way: its result lives in git remotes and the registry, not in a diff.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile

BENCH = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BENCH)
import grade as grader  # noqa: E402
import score as scorer  # noqa: E402

NOT_FROM_A_DIFF = {'e1-word-count'}


def benchmark_running():
    names = subprocess.run(['docker', 'ps', '--format', '{{.Names}}'], capture_output=True, text=True).stdout
    return any(n.startswith('bench-') for n in names.split())


def regrade(run_dir, ws):
    m = scorer.meta(run_dir)
    task = m.get('task') or os.path.basename(os.path.dirname(os.path.abspath(run_dir)))
    if task in NOT_FROM_A_DIFF:
        return f'{task} cannot be regraded from a diff'
    gdir = os.path.join(run_dir, 'grade')
    patch = os.path.join(gdir, 'diff.patch')
    if not os.path.exists(patch):
        return 'no grade/diff.patch'
    task_dir = os.path.join(BENCH, 'tasks', task)
    base = grader.task_env(task_dir)['BASE_TAG']
    subprocess.run([os.path.join(BENCH, 'reset.sh'), base, ws], check=True, stdout=subprocess.DEVNULL)
    prepare = os.path.join(task_dir, 'prepare.sh')
    if os.path.exists(prepare):
        subprocess.run([prepare, ws], check=True)
    # Apply a copy: grading rewrites diff.patch in place. An empty diff means the agent changed nothing.
    if os.path.getsize(patch):
        tmp = tempfile.NamedTemporaryFile(suffix='.patch', delete=False)
        shutil.copyfile(patch, tmp.name)
        res = subprocess.run(['git', 'apply', '--whitespace=nowarn', tmp.name], cwd=ws, capture_output=True, text=True)
        os.unlink(tmp.name)
        if res.returncode:
            return 'diff does not apply: ' + res.stderr.strip()
    auto = os.path.join(gdir, 'auto.json')
    if os.path.exists(auto):
        shutil.copyfile(auto, os.path.join(gdir, 'auto.prev.json'))
    grader.grade(task, gdir, ws, with_ng=bool(os.environ.get('GRADE_WITH_NG')))
    return None


def changes(run_dir):
    """Keys whose value changed between auto.prev.json and auto.json."""
    gdir = os.path.join(run_dir, 'grade')
    prev_path = os.path.join(gdir, 'auto.prev.json')
    prev = json.load(open(prev_path)) if os.path.exists(prev_path) else {}
    now = json.load(open(os.path.join(gdir, 'auto.json')))
    return {k: (prev.get(k), now.get(k)) for k in sorted(set(prev) | set(now)) if prev.get(k) != now.get(k)}


def main(args):
    ws = os.path.expanduser('~/llm-bench')
    if '--workspace' in args:
        i = args.index('--workspace')
        ws = os.path.abspath(args[i + 1])
        args = args[:i] + args[i + 2:]
    if not args:
        sys.exit(__doc__)
    if benchmark_running():
        sys.exit('A bench- container is running; regrading would reset its workspace. Try again later.')
    for run_dir in args:
        error = regrade(os.path.abspath(run_dir), ws)
        if error:
            print(f'{run_dir}: skipped, {error}')
            continue
        print(scorer.summary_line(scorer.score(run_dir)))
        for k, (before, after) in changes(run_dir).items():
            print(f'  {k}: {before!r} -> {after!r}')


if __name__ == '__main__':
    main(sys.argv[1:])
