#!/usr/bin/env python3
"""Lend the ChatGPT login of the chatgpt target to one run, and take it back afterwards.

Usage: bench/chatgpt_auth.py install <login-home> <run-codex-home>
       bench/chatgpt_auth.py collect <login-home> <run-codex-home>

The login is the auth.json that `codex login` wrote to <login-home> (CHATGPT_HOME, .codex-chatgpt/ by default):
a ChatGPT sign-in (tokens with a refresh token) or a personal access token (`codex login --with-access-token`).
install copies it into the run's Codex home. collect copies a sign-in back when Codex refreshed it during the run
(OpenAI refresh tokens are single use, so the old one no longer works), then deletes it from the run folder,
which run.sh keeps as the transcript. Runs of the chatgpt target must not overlap; load.sh refuses them.
"""
import json
import os
import sys
from pathlib import Path

AUTH = 'auth.json'


def read_login(path):
    """The login in an auth.json as a dict, or None when the file is missing or holds no ChatGPT login."""
    try:
        data = json.loads(path.read_text())
    except (OSError, ValueError):
        return None
    tokens = data.get('tokens') or {}
    return data if tokens.get('refresh_token') or data.get('personal_access_token') else None


def write_private(path, text):
    """Write text to path readable by the owner only, replacing it in one step."""
    tmp = path.with_name(path.name + '.tmp')
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, 'w') as f:
        f.write(text)
    os.replace(tmp, path)


def install(login_home, run_home):
    """Copy the login into the run's Codex home; exit with a hint when there is none."""
    src = login_home / AUTH
    if read_login(src) is None:
        sys.exit(f'no ChatGPT login in {src}. Log in once with: bin/cx chatgpt login --device-auth '
                 '(or --with-access-token, reading a personal access token from stdin)')
    run_home.mkdir(parents=True, exist_ok=True)
    write_private(run_home / AUTH, src.read_text())


def collect(login_home, run_home):
    """Keep a login Codex refreshed during the run, then remove the run's copy."""
    copy = run_home / AUTH
    lent = read_login(copy)
    kept = read_login(login_home / AUTH)
    if lent is not None and lent != kept and lent.get('last_refresh', '') >= (kept or {}).get('last_refresh', ''):
        write_private(login_home / AUTH, json.dumps(lent, indent=2))
        print(f'chatgpt_auth: Codex refreshed the login during the run; saved it to {login_home / AUTH}')
    copy.unlink(missing_ok=True)


def main(argv):
    """Dispatch install or collect."""
    if len(argv) != 3 or argv[0] not in ('install', 'collect'):
        sys.exit(__doc__)
    action, login_home, run_home = argv[0], Path(argv[1]), Path(argv[2])
    (install if action == 'install' else collect)(login_home, run_home)


if __name__ == '__main__':
    main(sys.argv[1:])
