#!/usr/bin/env python3
"""Grade a run's workspace with the task's checks and write <out-dir>/auto.json plus evidence files.

Usage: bench/grade.py <task-id> <out-dir> [workspace] [--with-ng]

Each task has a grade.py next to its prompt with one function, grade(g), that calls checks on g, a
lib/checks.py Grader. The workspace defaults to ~/llm-bench. The run folder is the parent of <out-dir>
(run.sh passes $RUN/grade); tasks that read turns or the registry log find them there.
--with-ng also runs the Angular build where a task has one (T1, T1b; needs about 1 GB free disk).
"""
import os
import sys

BENCH = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(BENCH, 'lib'))
import checks  # noqa: E402


def task_env(task_dir):
    """KEY=value lines of the task's task.env."""
    env = {}
    for line in checks.lines_of(checks.read_text(os.path.join(task_dir, 'task.env'))):
        if '=' in line and not line.lstrip().startswith('#'):
            k, v = line.split('=', 1)
            env[k.strip()] = v.strip()
    return env


def grade(task, out, ws, with_ng=False):
    task_dir = os.path.join(BENCH, 'tasks', task)
    g = checks.Grader(task_dir, os.path.abspath(out), os.path.abspath(ws), task_env(task_dir)['BASE_TAG'],
                      with_ng=with_ng)
    checks.import_file(os.path.join(task_dir, 'grade.py')).grade(g)
    return g.emit()


def main(args):
    with_ng = '--with-ng' in args
    args = [a for a in args if a != '--with-ng']
    if len(args) < 2:
        sys.exit(__doc__)
    ws = args[2] if len(args) > 2 else os.path.expanduser('~/llm-bench')
    print(grade(args[0], args[1], ws, with_ng), end='')


if __name__ == '__main__':
    main(sys.argv[1:])
