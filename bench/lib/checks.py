"""Shared grading checks. A task's grade.py gets a Grader and calls the checks it needs:

    def grade(g):
        g.record_diff(); g.diff_check(); g.comment_dashes()
        g.pc_compile('compile_ok')

Every check stores its result in g.r under the key it is given (or a fixed one, see each method) and may
write evidence files to g.out. bench/grade.py writes g.r as <out>/auto.json at the end.
Programs (git, ant, java, javac, jest, tsc) are run directly; all text processing happens here.
"""
import datetime
import importlib.util
import json
import locale
import os
import re
import shutil
import subprocess
import tempfile

LIB = os.path.dirname(os.path.abspath(__file__))
JAVA_HOME = '/usr/lib/jvm/java-17-openjdk-amd64'
JAVA = os.path.join(JAVA_HOME, 'bin', 'java')
JAVAC = os.path.join(JAVA_HOME, 'bin', 'javac')
RUN_TESTS = os.path.join(LIB, 'RunTests.java')
FAKE_REPO = os.path.join(LIB, 'java', 'FakeRepository.java')

# AGENTS.md: no dashes as punctuation in comments. Copyright lines are exempt: "2016 - 2026" is the prescribed format.
COMMENT_DASH = r'(//|<!--|/\*|^\+\s*\*|<#--).*( - |—|–)'
COPYRIGHT_LINE = r'(\(c\)|©|@author)'
# AGENTS.md: no issue numbers in identifiers
NUMBERED_IDENTIFIER = r'\b[A-Za-z_]+[0-9]{3,}[A-Za-z_]*\b'
NOT_AN_ISSUE_NUMBER = r'^(v[0-9]|UTF)'
COMMENT_LINE = r'^\+\s*(//|\*|/\*)'


# Text from git and from the workspace may not be UTF-8 (legacy Latin-1 sources). surrogateescape keeps every
# byte, so a diff written back out is byte for byte what git produced.
ENC = dict(encoding='utf-8', errors='surrogateescape')


def read_text(path):
    """File contents as text, or '' when the file is missing. Line endings are kept as they are."""
    try:
        with open(path, newline='', **ENC) as f:
            return f.read()
    except (FileNotFoundError, IsADirectoryError):
        return ''


def write_text(path, text):
    with open(path, 'w', newline='', **ENC) as f:
        f.write(text)


def import_file(path):
    """Imports a Python file that is not on the path (a task's hidden helpers, another task's grade.py)."""
    name = os.path.splitext(os.path.basename(path))[0].replace('-', '_')
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def locale_sorted(names):
    """Sorted the way a bash glob or ls sorts them (LC_COLLATE), which decides classpath order."""
    locale.setlocale(locale.LC_COLLATE, '')
    return sorted(names, key=locale.strxfrm)


def lines_of(text):
    """Lines without their newline, like grep sees them. A trailing newline adds no empty last line."""
    return text.split('\n')[:-1] if text.endswith('\n') else text.split('\n') if text else []


def grep(pattern, lines):
    """Lines matching a regular expression."""
    rx = re.compile(pattern)
    return [l for l in lines if rx.search(l)]


def grep_v(pattern, lines):
    """Lines not matching a regular expression."""
    rx = re.compile(pattern)
    return [l for l in lines if not rx.search(l)]


def last_match(pattern, text):
    """The last match of a pattern in a text, '' when there is none."""
    found = re.findall(pattern, text, re.M)
    return found[-1] if found else ''


def tests_summary(log_text):
    """The 'TESTS found=.. ok=.. fail=..' line RunTests prints."""
    return last_match(r'TESTS.*', log_text)


def jest_summary(log_text):
    """Jest's 'Tests: ...' line."""
    return last_match(r'^Tests:.*', log_text)


class Grader:
    def __init__(self, task_dir, out, ws, base, run_dir=None, with_ng=False):
        self.task_dir, self.out, self.ws, self.base = task_dir, out, ws, base
        self.run_dir = run_dir or os.path.dirname(os.path.abspath(out))
        self.with_ng = with_ng
        self.r = {}
        self.changed = []
        os.makedirs(out, exist_ok=True)
        os.environ['JAVA_HOME'] = JAVA_HOME  # ant picks the JDK from it

    # Helpers

    def path(self, rel):
        return os.path.join(self.ws, rel)

    def hidden(self, name):
        return os.path.join(self.task_dir, 'hidden', name)

    def write(self, name, lines):
        """Writes evidence lines to <out>/name and returns how many there were."""
        write_text(os.path.join(self.out, name), ''.join(l + '\n' for l in lines))
        return len(lines)

    def run(self, cmd, log, cwd=None, append=False, timeout=None, env=None):
        """Runs a program with stdout and stderr in <out>/log. True when it exits 0."""
        with open(os.path.join(self.out, log), 'a' if append else 'w') as f:
            try:
                return subprocess.run(cmd, cwd=cwd or self.ws, stdout=f, stderr=subprocess.STDOUT,
                                      timeout=timeout, env=env).returncode == 0
            except subprocess.TimeoutExpired:
                f.write(f'\n(timed out after {timeout} s)\n')
                return False

    def git(self, *args, repo=None, stderr=False):
        """Runs git in the workspace (or another repo) and returns its output, with stderr when asked."""
        res = subprocess.run(['git', '-C', repo or self.ws, *args], capture_output=True)
        return (res.stdout + (res.stderr if stderr else b'')).decode(**ENC)

    def git_ok(self, *args, repo=None):
        return subprocess.run(['git', '-C', repo or self.ws, *args], capture_output=True).returncode == 0

    def diff_text(self):
        return read_text(os.path.join(self.out, 'diff.patch'))

    def added_lines(self, path_regex=None):
        """Added lines of the agent's diff ('+' prefix kept). With path_regex: only in files whose '+++ ' header
        matches it, and without the headers."""
        out, keep = [], path_regex is None
        for line in lines_of(self.diff_text()):
            if line.startswith('+++ ') and path_regex is not None:
                keep = re.search(path_regex, line) is not None
                continue
            if keep and line.startswith('+'):
                out.append(line)
        return out

    def added_code_lines(self, path_regex):
        """Added lines in files matching path_regex, without comment lines and lines starting '++'."""
        lines = [l for l in self.added_lines(path_regex) if len(l) > 1 and l[1] != '+']
        return grep_v(COMMENT_LINE, lines)

    def changed_matching(self, pattern):
        return any(re.search(pattern, f) for f in self.changed)

    def count_in_tree(self, rel_dir, needle):
        """How often needle occurs in the files under a folder, like grep -rho needle | wc -l. Like grep, it
        skips symlinks and files that are binary or not valid UTF-8."""
        count = 0
        for root, _, files in os.walk(self.path(rel_dir)):
            for name in files:
                full = os.path.join(root, name)
                if os.path.islink(full):
                    continue
                data = open(full, 'rb').read()
                if b'\0' in data:
                    continue
                try:
                    count += data.decode('utf-8').count(needle)
                except UnicodeDecodeError:
                    pass
        return count

    # Diff and procedure

    def record_diff(self):
        """The agent's changes against the base tag, new files included: diff.patch, changed-files.txt."""
        self.git('add', '-A', '-N', '.')
        patch = self.git('diff', self.base)
        names = self.git('diff', '--name-only', self.base)
        self.git('reset', '-q')
        write_text(os.path.join(self.out, 'diff.patch'), patch)
        self.changed = lines_of(names)
        self.r['changed_files'] = self.write('changed-files.txt', self.changed)

    def diff_check(self, whitespace='-space-before-tab,cr-at-eol', include_untracked=True):
        """git diff --check: trailing whitespace and the like. Legacy space before tab indentation is ignored."""
        if include_untracked:
            self.git('add', '-A', '-N', '.')
        ok = self.run(['git', '-c', f'core.whitespace={whitespace}', 'diff', '--check', self.base], 'diff-check.txt')
        if include_untracked:
            self.git('reset', '-q')
        self.r['diff_check_clean'] = ok

    def comment_dashes(self, pattern=COMMENT_DASH):
        """Added comment lines with ' - ', an en or an em dash: comment-dashes.txt and comment_dash_lines."""
        added = [l for l in self.added_lines() if not l.startswith('+++')]
        hits = grep_v(COPYRIGHT_LINE, grep(pattern, added))
        self.r['comment_dash_lines'] = self.write('comment-dashes.txt', hits)

    def scope_check(self, allowed):
        """Changed files outside the allowed regex: out-of-scope.txt and out_of_scope_files."""
        self.r['out_of_scope_files'] = self.write('out-of-scope.txt', grep_v(allowed, self.changed))

    def copyright_year(self, key, rel, year=None):
        """The file's copyright range ends with this year (AGENTS.md: bump it when touching a file)."""
        year = year or datetime.date.today().year
        self.r[key] = re.search(rf'(\(c\)|©) *[0-9]{{4}} *- *{year}', read_text(self.path(rel))) is not None

    @staticmethod
    def _all_crlf(path):
        data = open(path, 'rb').read() if os.path.exists(path) else b''
        newlines = data.count(b'\n')
        pieces = data.split(b'\n')
        if data.endswith(b'\n'):
            pieces = pieces[:-1]
        crlf = sum(1 for p in pieces if p.endswith(b'\r'))
        return newlines > 0 and newlines == crlf, newlines == crlf

    def crlf_kept(self, key, rel):
        """A CRLF legacy file stays CRLF on every line."""
        self.r[key] = self._all_crlf(self.path(rel))[0]

    def crlf_kept_if_touched(self, key, rels):
        """CRLF files the agent touched stay CRLF. True when none of them was touched."""
        bad = [rel for rel in rels if rel in self.changed and not self._all_crlf(self.path(rel))[1]]
        if bad:
            self.write(f'{key}.txt', bad)
        self.r[key] = not bad

    def count_added(self, key, pattern, path_regex):
        """Added lines outside comments that match a pattern, in files matching path_regex."""
        self.r[key] = len(grep(pattern, self.added_code_lines(path_regex)))

    def numbered_identifiers(self, new_since=None):
        """Identifiers with 3+ digit numbers in added lines: numbered-identifiers.txt and numbered_identifiers.
        With new_since (a tag), identifiers that already existed in pc/source at that tag are left out."""
        ids = set()
        for line in self.added_lines():
            ids.update(m.group(0) for m in re.finditer(NUMBERED_IDENTIFIER, line))
        ids = sorted(i for i in ids if not re.search(NOT_AN_ISSUE_NUMBER, i))
        if new_since:
            ids = [i for i in ids if not self.git_ok('grep', '-qw', i, new_since, '--', 'pc/source')]
        self.r['numbered_identifiers'] = self.write('numbered-identifiers.txt', ids)

    def wrote_tests(self, prefix='pc/test/'):
        """agent_wrote_tests: the agent added or changed something under the test folder."""
        self.r['agent_wrote_tests'] = any(f.startswith(prefix) for f in self.changed)

    # Builds and tests

    def pc_compile(self, key):
        """Clean compile of the pc slice. A clean build, because ant can skip a source edited right after a build."""
        self.r[key] = self.run(['ant', 'clean', 'compile', '-DcleanServerSrcCopies=true'], 'compile.log',
                               cwd=self.path('pc'))

    def pc_classpath(self, relative=False):
        root = 'pc' if relative else self.path('pc')
        lib = self.path('pc/commons-lib')
        jars = locale_sorted(f for f in os.listdir(lib) if f.endswith('.jar')) if os.path.isdir(lib) else []
        return ':'.join([os.path.join(root, 'classes')] + [os.path.join(root, 'commons-lib', j) for j in jars])

    def junit(self, key, test_files, classes, log=None, summary_key=None, java_props=(), relative_cp=False):
        """Compiles test files with RunTests against pc/classes and runs the given classes. Sets key and
        key_summary. relative_cp gives a classpath relative to the workspace (which is the working folder)."""
        log = log or f'{key}.log'
        cp = self.pc_classpath(relative_cp)
        tmp = tempfile.mkdtemp()
        try:
            ok = self.run([JAVAC, '-cp', cp, '-d', tmp, RUN_TESTS, *test_files], log) and \
                self.run([JAVA, *java_props, '-cp', f'{tmp}:{cp}', 'RunTests', *classes], log, append=True)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
        self.r[key] = ok
        self.r[summary_key or f'{key}_summary'] = tests_summary(read_text(os.path.join(self.out, log)))

    def ts_dir(self):
        return self.path('the app/TypeScript')

    def ts_typecheck(self, key):
        """Type check over the whole TypeScript tsconfig."""
        self.r[key] = self.run(['./node_modules/.bin/tsc', '--noEmit'], f'{key}.log', cwd=self.ts_dir())

    def ts_jest_all(self, key):
        """The TypeScript Jest suite as the agent left it, its own tests included."""
        self.r[key] = self.run(['./node_modules/.bin/jest', '--config=jest.config.js', '--silent'], f'{key}.log',
                               cwd=self.ts_dir())
        self.r[f'{key}_summary'] = jest_summary(read_text(os.path.join(self.out, f'{key}.log')))

    def hidden_jest(self, key, files):
        """Hidden Jest tests, run from inside TypeScript/tests (relative imports need that) and removed after."""
        names = []
        for f in files:
            shutil.copy(f, os.path.join(self.ts_dir(), 'tests'))
            names.append('tests/' + os.path.basename(f))
        try:
            self.r[key] = self.run(['./node_modules/.bin/jest', '--config=jest.config.js', '--silent', '--verbose',
                                    *names], f'{key}.log', cwd=self.ts_dir())
        finally:
            for n in names:
                os.remove(os.path.join(self.ts_dir(), n))
        self.r[f'{key}_summary'] = jest_summary(read_text(os.path.join(self.out, f'{key}.log')))

    def emit(self):
        """Writes auto.json, keys sorted, and returns its text."""
        text = json.dumps(dict(sorted(self.r.items())), indent=2) + '\n'
        write_text(os.path.join(self.out, 'auto.json'), text)
        return text
