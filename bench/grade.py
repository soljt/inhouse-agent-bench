#!/usr/bin/env python3
"""Grade a run's workspace with the task's checks and write <out-dir>/auto.json plus evidence files.

Usage: bench/grade.py <task-id> <out-dir> [workspace] [--with-ng]

Grading runs the agent's build files and tests, so it happens in a throwaway container without network
(bench/sandbox.py). Besides the workspace, the container sees the task folders, bench/lib and the run
folder read-only, and only <out-dir> writable.

Each task has a grade.py next to its prompt with one function, grade(g), that calls checks on g, a
lib/checks.py Grader. The task set comes from BENCH_TASKSET (see lib/taskset.py); the workspace defaults to
the task set's. The run folder is the parent of <out-dir> (run.sh passes $RUN/grade); tasks that read turns
or the registry log find them there.
--with-ng also runs the Angular build where a task has one (T1, T1b; needs about 1 GB free disk).
"""
import os
import sys

BENCH = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BENCH)
sys.path.insert(0, os.path.join(BENCH, 'lib'))
import checks  # noqa: E402
import taskset  # noqa: E402


def task_env(task_dir):
    """KEY=value lines of the task's task.env."""
    env = {}
    for line in checks.lines_of(checks.read_text(os.path.join(task_dir, 'task.env'))):
        if '=' in line and not line.lstrip().startswith('#'):
            k, v = line.split('=', 1)
            env[k.strip()] = v.strip()
    return env


def grade(task, out, ws, with_ng=False):
    task_dir = taskset.task_dir(task)
    grader = taskset.grader_class()
    g = grader(task_dir, os.path.abspath(out), os.path.abspath(ws), task_env(task_dir)['BASE_TAG'], with_ng=with_ng)
    taskset.task_module(task).grade(g)
    return g.emit()


def grade_in_sandbox(task, out, ws, with_ng=False, **kwargs):
    """Runs this script inside the sandbox for one run and returns its exit code. kwargs go to subprocess.run
    (stdout=DEVNULL to drop the auto.json it prints)."""
    import sandbox  # host only: the container gets lib/, tasks/ and this file, not sandbox.py
    out, ws = os.path.abspath(out), os.path.abspath(ws)
    os.makedirs(out, exist_ok=True)  # docker would create a missing mount point owned by root
    cmd = ['python3', os.path.abspath(__file__), task, out, ws, '--in-sandbox'] + (['--with-ng'] if with_ng else [])
    root = taskset.root()
    ro = [os.path.join(BENCH, 'lib'), root, os.path.abspath(__file__), os.path.dirname(out)]
    return sandbox.run(cmd, ws, ro=ro, rw=[out], env={'BENCH_TASKSET': root}, **kwargs).returncode


def main(args):
    with_ng = '--with-ng' in args
    in_sandbox = '--in-sandbox' in args
    args = [a for a in args if a not in ('--with-ng', '--in-sandbox')]
    if len(args) < 2:
        sys.exit(__doc__)
    ws = args[2] if len(args) > 2 else taskset.config()['workspace']
    if not in_sandbox:
        sys.exit(grade_in_sandbox(args[0], args[1], ws, with_ng))
    print(grade(args[0], args[1], ws, with_ng), end='')


if __name__ == '__main__':
    main(sys.argv[1:])
