#!/usr/bin/env python3
"""Create the example workspace: a git repo with a tiny Python package and the tag the tasks start from.

Usage: python3 make_workspace.py [path]      (default: the workspace in taskset.toml, ~/llm-bench-example)

Re-running replaces nothing: it stops if the folder exists. Delete it first to start over.
"""
import os
import subprocess
import sys
import tomllib

HERE = os.path.dirname(os.path.abspath(__file__))
TAG = 'example-base'

FILES = {
    'AGENTS.md': '''# Rules for this repository

- Reuse the helpers in `textstats/text.py`; don't write new whitespace handling.
- Every public function has type hints and a one line docstring.
- Every fix or feature comes with a test in `tests/`.
- Run the tests with `python3 -m pytest -q`.
''',
    'textstats/__init__.py': '''"""Small text statistics."""
from .text import normalize_whitespace
from .words import word_count

__all__ = ['normalize_whitespace', 'word_count']
''',
    'textstats/text.py': '''"""Text helpers shared by the package."""
import re


def normalize_whitespace(text: str) -> str:
    """Collapse runs of whitespace into single spaces and strip both ends."""
    return re.sub(r'\\s+', ' ', text).strip()
''',
    'textstats/words.py': '''"""Word statistics."""


def word_count(text: str) -> int:
    """Number of words in text."""
    if not text:
        return 0
    return len(text.split(' '))
''',
    'tests/test_words.py': '''from textstats import word_count


def test_counts_words():
    assert word_count('one two three') == 3


def test_empty_text_has_no_words():
    assert word_count('') == 0
''',
    'tests/test_text.py': '''from textstats import normalize_whitespace


def test_collapses_and_strips():
    assert normalize_whitespace('  a \\t b\\n') == 'a b'
''',
}


def git(ws, *args):
    subprocess.run(['git', '-C', ws, *args], check=True, capture_output=True)


def main(args):
    config = tomllib.load(open(os.path.join(HERE, 'taskset.toml'), 'rb'))
    ws = os.path.abspath(os.path.expanduser(args[0] if args else config['workspace']))
    if os.path.exists(ws):
        sys.exit(f'{ws} exists; delete it to create it again')
    for rel, text in FILES.items():
        os.makedirs(os.path.dirname(os.path.join(ws, rel)) or ws, exist_ok=True)
        open(os.path.join(ws, rel), 'w').write(text)
    open(os.path.join(ws, '.gitignore'), 'w').write('__pycache__/\n.pytest_cache/\n')
    git(ws, 'init', '-q', '-b', 'main')
    git(ws, 'add', '-A')
    git(ws, '-c', 'user.name=Example', '-c', 'user.email=example@example.invalid', 'commit', '-q', '-m', 'textstats')
    git(ws, 'tag', TAG)
    print(f'created {ws} with tag {TAG}')


if __name__ == '__main__':
    main(sys.argv[1:])
