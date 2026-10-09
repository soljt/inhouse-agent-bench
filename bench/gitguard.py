#!/usr/bin/env python3
"""Guards the bench workspace's git metadata against changes made from inside a run.

Usage:
  bench/gitguard.py check <workspace> [--run <run-dir>] [--before]
  bench/gitguard.py accept <workspace>

The agent, and the grader running the agent's build files, can write to the workspace's .git. A hook, or
a config entry such as core.fsmonitor, a filter driver or a diff command, would then run on the host the
next time git runs there (reset.sh, load.sh). Host git calls already pass -c core.hooksPath=/dev/null and
-c core.fsmonitor=false; this guard covers everything else.

Guarded: hooks/ and info/attributes (exact), the config and each worktree's config.worktree (every key
except harmless ones like branch.* and remote.*.url, which you change yourself when pushing from another
worktree). The baseline is kept in ~/.local/state/llm-bench/gitguard/ and is created on the first check.

check: harmless drift updates the baseline. Any other difference:
  --before (start of a run): nothing is changed, exit 2. Look at it, then restore it or run accept.
  otherwise: the changed files are saved to <run-dir>/gitguard/, the baseline is restored,
             git_tampered=1 goes into <run-dir>/meta.txt, exit 1.
accept: takes the current state as the new baseline.
"""
import hashlib
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile

STATE = os.path.join(os.environ.get('XDG_STATE_HOME', os.path.expanduser('~/.local/state')), 'llm-bench', 'gitguard')

# Config keys that cannot make git run a program. Everything else counts.
HARMLESS_KEYS = [
    r'^branch\.', r'^remote\.[^.]+\.(url|pushurl|fetch|push|mirror|tagopt|prune)$', r'^user\.',
    r'^core\.(repositoryformatversion|filemode|bare|logallrefupdates|ignorecase|precomposeunicode|symlinks)$',
    r'^extensions\.', r'^pull\.', r'^push\.default$', r'^init\.', r'^gc\.',
]


def git_dir(ws):
    """The common git folder of a workspace, found without running git."""
    dot_git = os.path.join(ws, '.git')
    if os.path.isdir(dot_git):
        return dot_git
    gitdir = open(dot_git).read().strip().removeprefix('gitdir:').strip()
    gitdir = os.path.normpath(os.path.join(ws, gitdir))
    commondir = os.path.join(gitdir, 'commondir')
    if os.path.exists(commondir):
        return os.path.normpath(os.path.join(gitdir, open(commondir).read().strip()))
    return gitdir


def guarded_files(gdir):
    """Relative paths of the guarded files that exist now."""
    files = ['config', 'info/attributes']
    for root, _, names in os.walk(os.path.join(gdir, 'hooks')):
        files += [os.path.relpath(os.path.join(root, n), gdir) for n in names]
    worktrees = os.path.join(gdir, 'worktrees')
    if os.path.isdir(worktrees):
        files += [f'worktrees/{w}/config.worktree' for w in os.listdir(worktrees)]
    return sorted(f for f in files if os.path.isfile(os.path.join(gdir, f)))


def is_config(rel):
    return rel == 'config' or rel.endswith('config.worktree')


def config_entries(path):
    """The config's key=value entries that could run something, sorted. None if git cannot parse it.
    git config --file only parses the file; it runs nothing and follows no includes."""
    res = subprocess.run(['git', 'config', '--file', path, '--list'], capture_output=True, text=True)
    if res.returncode:
        return None
    entries = [e for e in res.stdout.splitlines() if not any(re.search(h, e.split('=', 1)[0]) for h in HARMLESS_KEYS)]
    return sorted(entries)


def fingerprint(gdir):
    """What the guard compares: exact content for hooks and attributes, relevant entries for configs."""
    out = {}
    for rel in guarded_files(gdir):
        path = os.path.join(gdir, rel)
        if is_config(rel):
            out[rel] = config_entries(path)
        else:
            out[rel] = hashlib.sha256(open(path, 'rb').read()).hexdigest()
    return out


def baseline_dir(gdir):
    return os.path.join(STATE, hashlib.sha256(os.path.abspath(gdir).encode()).hexdigest()[:16])


def save_baseline(gdir):
    """Copies the guarded files and their fingerprint to the state folder, replacing the old baseline."""
    base = baseline_dir(gdir)
    os.makedirs(STATE, exist_ok=True)
    tmp = tempfile.mkdtemp(dir=STATE)
    for rel in guarded_files(gdir):
        os.makedirs(os.path.dirname(os.path.join(tmp, 'files', rel)), exist_ok=True)
        shutil.copy2(os.path.join(gdir, rel), os.path.join(tmp, 'files', rel))
    json.dump({'git_dir': os.path.abspath(gdir), 'fingerprint': fingerprint(gdir)},
              open(os.path.join(tmp, 'baseline.json'), 'w'), indent=1)
    # load.sh checks several worktrees of one repo at once; if another check saved first, keep that one
    try:
        shutil.rmtree(base, ignore_errors=True)
        os.rename(tmp, base)
    except OSError:
        shutil.rmtree(tmp, ignore_errors=True)


def baseline_is_current(gdir):
    """True when the stored copies match the guarded files byte for byte."""
    files = os.path.join(baseline_dir(gdir), 'files')
    stored = {os.path.relpath(os.path.join(r, n), files) for r, _, ns in os.walk(files) for n in ns}
    if stored != set(guarded_files(gdir)):
        return False
    return all(open(os.path.join(files, rel), 'rb').read() == open(os.path.join(gdir, rel), 'rb').read()
               for rel in stored)


def restore_baseline(gdir):
    """Puts the baseline's files back and removes guarded files the baseline does not have (new hooks)."""
    files = os.path.join(baseline_dir(gdir), 'files')
    kept = set()
    for root, _, names in os.walk(files):
        for n in names:
            rel = os.path.relpath(os.path.join(root, n), files)
            kept.add(rel)
            os.makedirs(os.path.dirname(os.path.join(gdir, rel)), exist_ok=True)
            shutil.copy2(os.path.join(root, n), os.path.join(gdir, rel))
    for rel in guarded_files(gdir):
        if rel not in kept:
            os.remove(os.path.join(gdir, rel))


def differences(gdir):
    """Guarded paths whose fingerprint differs from the baseline."""
    base = json.load(open(os.path.join(baseline_dir(gdir), 'baseline.json')))['fingerprint']
    now = fingerprint(gdir)
    return sorted(rel for rel in set(base) | set(now) if base.get(rel) != now.get(rel))


def check(ws, run_dir=None, before=False):
    gdir = git_dir(ws)
    if not os.path.exists(os.path.join(baseline_dir(gdir), 'baseline.json')):
        save_baseline(gdir)
        return 0
    changed = differences(gdir)
    if not changed:
        if not baseline_is_current(gdir):
            save_baseline(gdir)  # take harmless drift (a new branch, a remote) into the baseline
        return 0
    print(f'gitguard: {gdir} changed: {", ".join(changed)}', file=sys.stderr)
    if before:
        print(f'gitguard: not starting. Inspect those files, then restore them or run: bench/gitguard.py accept {ws}',
              file=sys.stderr)
        return 2
    if run_dir:
        evidence = os.path.join(run_dir, 'gitguard')
        for rel in changed:
            if os.path.exists(os.path.join(gdir, rel)):
                os.makedirs(os.path.dirname(os.path.join(evidence, rel)), exist_ok=True)
                shutil.copy2(os.path.join(gdir, rel), os.path.join(evidence, rel))
        with open(os.path.join(run_dir, 'meta.txt'), 'a') as f:
            f.write('git_tampered=1\n')
        print(f'gitguard: changed files saved to {evidence}', file=sys.stderr)
    restore_baseline(gdir)
    print('gitguard: baseline restored', file=sys.stderr)
    return 1


def main(args):
    if len(args) < 2 or args[0] not in ('check', 'accept'):
        sys.exit(__doc__)
    ws = os.path.abspath(args[1])
    if args[0] == 'accept':
        save_baseline(git_dir(ws))
        print(f'gitguard: baseline for {git_dir(ws)} saved')
        return 0
    run_dir = args[args.index('--run') + 1] if '--run' in args else None
    return check(ws, run_dir, before='--before' in args)


if __name__ == '__main__':
    sys.exit(main(sys.argv[1:]))
