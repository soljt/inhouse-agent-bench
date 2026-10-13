"""Which tests passed and failed, read back from a grader's test log, for the review page.

Understands the logs the generic checks write: RunTests (Grader.junit: JUnit's failure list and its
'TESTS found=.. ok=.. fail=..' line, with javac errors when the tests did not compile), Jest with --verbose
(the check and cross lines and the failure blocks) and pytest (its FAILED and ERROR lines). Anything else
comes back as None and the review page shows the log as it is.
"""
import re

JUNIT_SUMMARY = re.compile(r'TESTS found=(\d+) ok=(\d+) fail=(\d+)')
JUNIT_FAILURE = re.compile(r'^  JUnit [^:]*:([^:]+):(.+)$')
JAVAC_ERROR = re.compile(r'^\S+\.java:\d+: error: ')
JEST_RESULT = re.compile(r'^\s+([✓✕○])\s+(.+?)(?: \(\d+ ms\))?$')
JEST_BLOCK = re.compile(r'^  ● (.+)$')
JEST_SUMMARY = re.compile(r'^Tests:\s+(.*)$', re.M)
PYTEST_FAILED = re.compile(r'^(FAILED|ERROR) (\S+)(?: - (.*))?$')
PYTEST_SUMMARY = re.compile(r'^=*\s*(\d+ (?:passed|failed|errors?).*?)\s*=*$', re.M)
DETAIL_LINES = 12
# JUnit failure lines that say nothing about the agent's code: launcher frames and the method source line
NOISE = re.compile(r'^(MethodSource \[|java\.base/|jdk\.internal\.|org\.junit\.|org\.opentest4j\.|\[\.\.\.\])')


def junit(text):
    """RunTests output: the failed tests with their exception and first stack lines."""
    m = JUNIT_SUMMARY.search(text)
    lines = text.splitlines()
    if not m:
        errors = [i for i, line in enumerate(lines) if JAVAC_ERROR.match(line)]
        if not errors:
            return None
        detail = [l for i in errors[:5] for l in lines[i:i + 3]]
        return {'summary': f'did not compile ({len(errors)} javac errors)', 'passed': [], 'failed': [],
                'compile_errors': '\n'.join(detail)}
    failed, current = [], None
    for line in lines:
        f = JUNIT_FAILURE.match(line)
        if f:
            current = {'name': f'{f.group(1)}.{f.group(2)}', 'detail': []}
            failed.append(current)
        elif current is not None and line.startswith('    ') and not NOISE.match(line.strip()):
            if len(current['detail']) < DETAIL_LINES:
                current['detail'].append(line.strip())
        elif current is not None and not line.startswith('    '):
            current = None
    found, ok, fail = (int(x) for x in m.groups())
    for f in failed:
        f['detail'] = '\n'.join(f['detail'])
    return {'summary': f'{found} found, {ok} passed, {fail} failed', 'passed': [], 'failed': failed,
            'compile_errors': ''}


def jest(text):
    """Jest --verbose output: every test by name, and the failure block of each failed one."""
    lines = text.splitlines()
    results = [JEST_RESULT.match(l) for l in lines]
    results = [r for r in results if r]
    if not results and not JEST_SUMMARY.search(text):
        return None
    blocks, current = {}, None
    for line in lines:
        b = JEST_BLOCK.match(line)
        if b:
            current = b.group(1).split(' › ')[-1]
            blocks[current] = []
        elif current is not None and len(blocks[current]) < DETAIL_LINES and line.strip():
            blocks[current].append(line.rstrip())
    failed = [{'name': r.group(2), 'detail': '\n'.join(blocks.get(r.group(2), []))} for r in results if r.group(1) == '✕']
    summary = JEST_SUMMARY.search(text)
    return {'summary': summary.group(1).strip() if summary else f'{len(results)} tests',
            'passed': [r.group(2) for r in results if r.group(1) == '✓'], 'failed': failed, 'compile_errors': ''}


def pytest(text):
    """pytest -q output: the FAILED and ERROR lines with their messages."""
    summary = PYTEST_SUMMARY.search(text)
    failed = [PYTEST_FAILED.match(l) for l in text.splitlines()]
    failed = [{'name': f.group(2), 'detail': f.group(3) or f.group(1)} for f in failed if f]
    if not summary and not failed:
        return None
    return {'summary': summary.group(1) if summary else f'{len(failed)} failed', 'passed': [], 'failed': failed,
            'compile_errors': ''}


def parse(text):
    """{'summary', 'passed', 'failed': [{'name', 'detail'}], 'compile_errors'} for a test log, or None."""
    for reader in (junit, jest, pytest):
        result = reader(text or '')
        if result:
            return result
    return None
