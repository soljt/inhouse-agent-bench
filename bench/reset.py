#!/usr/bin/env python3
"""Reset a bench workspace to a task's base tag, then run the task set's after-reset hook.

Usage: bench/reset.py <tag> [workspace]

The workspace defaults to the task set's (taskset.toml). git runs on the host, without the agent's hooks
or fsmonitor command (the agent can write the workspace's .git, see gitguard.py). The hook executes
workspace files (build files, generators), so it runs in the sandbox: no network, deps read-only.
Afterwards the workspace must be clean, so the agent starts from the same state every time.
"""
import os
import subprocess
import sys

BENCH = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BENCH)
sys.path.insert(0, os.path.join(BENCH, 'lib'))
import sandbox  # noqa: E402
import taskset  # noqa: E402
from gitguard import HOST_GIT  # noqa: E402


def git(ws, *args):
    return subprocess.run([*HOST_GIT, '-C', ws, *args], check=True, capture_output=True, text=True).stdout


def reset(tag, ws=None):
    c = taskset.config()
    ws = os.path.abspath(ws or c['workspace'])
    git(ws, 'checkout', '-q', '-f', '--detach', tag)
    git(ws, 'reset', '-q', '--hard', tag)
    git(ws, 'clean', '-q', '-ffdx', *[arg for k in c['reset_keep'] for arg in ('-e', k)])
    if c['reset_after']:
        res = sandbox.run([c['reset_after']], ws, ro=[c['root']], env={'WS': ws, 'DEPS': c['deps']})
        if res.returncode:
            sys.exit(f'reset: {c["reset_after"]} failed ({res.returncode})')
    left = [l for l in git(ws, 'status', '--short').splitlines() if not l.startswith('!!')]
    if left:
        sys.exit('reset left changes:\n' + '\n'.join(left))
    print(f'workspace {ws} reset to {tag}')


if __name__ == '__main__':
    if len(sys.argv) < 2:
        sys.exit(__doc__)
    reset(sys.argv[1], sys.argv[2] if len(sys.argv) > 2 else None)
