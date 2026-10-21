#!/usr/bin/env python3
"""Give one run its own git folder holding only the task's base tag and that tag's history.

Usage: bench/agentgit.py <workspace> <tag> <run-dir>

The workspace's own .git holds every task's base tag, the task set's development branches and whatever an
earlier run left there, so an agent running git log --all could read another task's answer (the T12 review
commit is T8's merged fix) or an earlier run's branches. run.sh mounts the folder made here over /work/.git
instead: the agent sees the base commit with its ancestors and nothing else, and anything it commits, branches
or tags stays in the run folder. The real .git is not visible in the container at all.

Prints the docker -v arguments for run.sh, one per line. A workspace whose .git is a folder gets the run's git
folder mounted over it. A worktree (bench/load.sh), whose .git is a file, gets a file pointing at the run's
git folder mounted over that file, and the folder at /run/agent-git.

Graders are unaffected: they diff the workspace's files against the base tag with the workspace's real .git.
"""
import os
import shutil
import subprocess
import sys

BENCH = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BENCH)
from gitguard import HOST_GIT  # noqa: E402

GIT_DIR = 'agent-git'
LINK_FILE = 'agent-git.link'
CONTAINER_GIT_DIR = '/run/agent-git'


def git(git_dir, *args, work_tree=None):
    """Run host git on the run's git folder (and the workspace as its work tree, when given)."""
    cmd = [*HOST_GIT, f'--git-dir={git_dir}']
    if work_tree:
        cmd.append(f'--work-tree={work_tree}')
    return subprocess.run([*cmd, *args], check=True, capture_output=True, text=True).stdout


def make(ws, tag, run_dir):
    """Create <run-dir>/agent-git with the tag's history only, HEAD detached at the tag, index matching it."""
    dest = os.path.join(run_dir, GIT_DIR)
    shutil.rmtree(dest, ignore_errors=True)
    subprocess.run(['git', 'init', '-q', '--bare', dest], check=True)
    git(dest, 'config', 'core.bare', 'false')
    git(dest, 'config', 'core.logallrefupdates', 'true')
    # Only the tag travels: --no-tags keeps the other tags out, and fetching a tag brings its ancestors only
    git(dest, 'fetch', '-q', '--no-tags', '--no-write-fetch-head', os.path.abspath(ws),
        f'refs/tags/{tag}:refs/tags/{tag}')
    commit = git(dest, 'rev-parse', f'refs/tags/{tag}^{{commit}}').strip()
    git(dest, 'update-ref', '--no-deref', 'HEAD', commit)
    # The workspace was just reset to the tag, so an index of the tag's tree describes it; refresh the stat data
    git(dest, 'read-tree', commit, work_tree=ws)
    subprocess.run([*HOST_GIT, f'--git-dir={dest}', f'--work-tree={ws}', 'update-index', '-q', '--refresh'],
                   capture_output=True)
    return dest


def mounts(ws, run_dir, dest):
    """The docker -v arguments that put the run's git folder where git in /work looks for it."""
    if os.path.isdir(os.path.join(ws, '.git')):
        return ['-v', f'{dest}:/work/.git']
    link = os.path.join(run_dir, LINK_FILE)
    with open(link, 'w') as f:
        f.write(f'gitdir: {CONTAINER_GIT_DIR}\n')
    return ['-v', f'{link}:/work/.git:ro', '-v', f'{dest}:{CONTAINER_GIT_DIR}']


def main(argv):
    """Make the run's git folder and print the mounts for run.sh."""
    if len(argv) != 3:
        sys.exit(__doc__)
    ws, tag, run_dir = (os.path.abspath(argv[0]), argv[1], os.path.abspath(argv[2]))
    dest = make(ws, tag, run_dir)
    print('\n'.join(mounts(ws, run_dir, dest)))


if __name__ == '__main__':
    main(sys.argv[1:])
