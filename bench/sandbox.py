#!/usr/bin/env python3
"""Run a command against a bench workspace in a throwaway container with no network.

Usage: bench/sandbox.py <workspace> [--ro PATH] [--rw PATH] [-w DIR] -- <command> [args...]

Anything that executes files the agent could have changed (ant build files, jest configs, tests, the
node_modules the workspace points at) runs here instead of on the host: grading and the task set's
after-reset hook (reset.py). The container is the task set's image (taskset.toml), runs as your uid, and sees
  - the workspace, read-write, at its host path (absolute paths and worktree links keep working)
  - the task set's deps folder, read-only
  - the workspace's common .git folder, read-only, when it lives outside the workspace (worktrees)
  - each --ro / --rw path at its host path
The exit code is the command's.
"""
import os
import subprocess
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), 'lib'))
import taskset  # noqa: E402
from gitguard import git_dir  # noqa: E402


def mounts(ws, ro=(), rw=()):
    """docker -v arguments for the workspace, the deps, the shared .git and any extra paths."""
    ws = os.path.abspath(ws)
    deps = taskset.config()['deps']
    out = ['-v', f'{ws}:{ws}', '-v', f'{deps}:{deps}:ro']
    common = os.path.abspath(git_dir(ws))
    if not common.startswith(ws + os.sep):
        out += ['-v', f'{common}:{common}:ro']
    for p in ro:
        out += ['-v', f'{os.path.abspath(p)}:{os.path.abspath(p)}:ro']
    for p in rw:
        out += ['-v', f'{os.path.abspath(p)}:{os.path.abspath(p)}']
    return out


def run(cmd, ws, ro=(), rw=(), workdir=None, env=None, **kwargs):
    """Runs cmd in the sandbox and returns the CompletedProcess. env: extra variables for the command.
    kwargs go to subprocess.run."""
    env_args = [arg for k, v in (env or {}).items() for arg in ('-e', f'{k}={v}')]
    docker = ['docker', 'run', '--rm', '--network', 'none', '--user', f'{os.getuid()}:{os.getgid()}',
              '--cap-drop', 'ALL', '--security-opt', 'no-new-privileges', '-e', 'HOME=/tmp', *env_args,
              *mounts(ws, ro, rw), '-w', os.path.abspath(workdir or ws), '--entrypoint', cmd[0],
              taskset.config()['image'], *cmd[1:]]
    return subprocess.run(docker, **kwargs)


def main(args):
    if '--' not in args:
        sys.exit(__doc__)
    opts, cmd = args[:args.index('--')], args[args.index('--') + 1:]
    if not opts or not cmd:
        sys.exit(__doc__)
    ws, ro, rw, workdir = opts[0], [], [], None
    i = 1
    while i < len(opts):
        flag, value = opts[i], opts[i + 1]
        if flag == '--ro':
            ro.append(value)
        elif flag == '--rw':
            rw.append(value)
        elif flag == '-w':
            workdir = value
        else:
            sys.exit(f'unknown option {flag}')
        i += 2
    return run(cmd, ws, ro, rw, workdir).returncode


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
