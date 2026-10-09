"""Generic grading checks. A task's grade.py gets a Grader and calls the checks it needs:

    def grade(g):
        g.record_diff(); g.diff_check()
        g.scope_check(r'^src/')
        g.command('hidden_test_ok', ['python3', '-m', 'pytest', '-q', g.hidden('test_hidden.py')])

Every check stores its result in g.r under the key it is given (or a fixed one, see each method) and may
write evidence files to g.out. bench/grade.py writes g.r as <out>/auto.json at the end.
Programs (git, java, pytest, ...) are run directly; all text processing happens here.

A task set can subclass Grader in its own lib/ module (taskset.toml: module) to add checks for its code
base and rules, and set the class attributes below.
"""
import importlib.util
import json
import locale
import os
import re
import shutil
import subprocess
import tempfile

LIB = os.path.dirname(os.path.abspath(__file__))
# A JUnit Platform launcher: compiles with the tests and runs the given classes (see Grader.junit)
RUN_TESTS = os.path.join(LIB, 'RunTests.java')


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
    # An added line that is a comment, for count_added (C-like languages by default)
    COMMENT_LINE = r'^\+\s*(//|\*|/\*)'
    # core.whitespace for diff_check, None for git's default
    DIFF_CHECK_WHITESPACE = None
    # JDK for junit (and exported for build tools); None uses java and javac from PATH
    JAVA_HOME = None

    def __init__(self, task_dir, out, ws, base, run_dir=None, with_ng=False):
        self.task_dir, self.out, self.ws, self.base = task_dir, out, ws, base
        self.run_dir = run_dir or os.path.dirname(os.path.abspath(out))
        self.with_ng = with_ng
        self.r = {}
        self.changed = []
        os.makedirs(out, exist_ok=True)
        if self.JAVA_HOME:
            os.environ['JAVA_HOME'] = self.JAVA_HOME  # build tools like ant pick the JDK from it

    def java_tool(self, name):
        return os.path.join(self.JAVA_HOME, 'bin', name) if self.JAVA_HOME else name

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

    def git(self, *args, repo=None, stderr=False, env=None):
        """Runs git in the workspace (or another repo) and returns its output, with stderr when asked."""
        res = subprocess.run(['git', '-C', repo or self.ws, *args], capture_output=True, env=env)
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
        return grep_v(self.COMMENT_LINE, lines)

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

    def scratch_git(self):
        """Environment for git with a temporary index and object folder, and that temp folder (remove it after).
        git add -N writes both; this way grading never changes the workspace's .git, which is read only in
        the sandbox when the workspace is a worktree. The real objects stay readable as an alternate."""
        def git_path(name):
            p = self.git('rev-parse', '--git-path', name).strip()
            return p if os.path.isabs(p) else os.path.join(self.ws, p)
        tmp = tempfile.mkdtemp()
        if os.path.exists(git_path('index')):
            shutil.copyfile(git_path('index'), os.path.join(tmp, 'index'))
        os.makedirs(os.path.join(tmp, 'objects'))
        env = dict(os.environ, GIT_INDEX_FILE=os.path.join(tmp, 'index'),
                   GIT_OBJECT_DIRECTORY=os.path.join(tmp, 'objects'), GIT_ALTERNATE_OBJECT_DIRECTORIES=git_path('objects'))
        return env, tmp

    def record_diff(self):
        """The agent's changes against the base tag, new files included: diff.patch, changed-files.txt."""
        env, tmp = self.scratch_git()
        self.git('add', '-A', '-N', '.', env=env)
        patch = self.git('diff', self.base, env=env)
        names = self.git('diff', '--name-only', self.base, env=env)
        shutil.rmtree(tmp)
        write_text(os.path.join(self.out, 'diff.patch'), patch)
        self.changed = lines_of(names)
        self.r['changed_files'] = self.write('changed-files.txt', self.changed)

    def diff_check(self, whitespace=None, include_untracked=True):
        """git diff --check: trailing whitespace and the like, with core.whitespace from the argument or
        DIFF_CHECK_WHITESPACE."""
        whitespace = whitespace or self.DIFF_CHECK_WHITESPACE
        env, tmp = self.scratch_git()
        if include_untracked:
            self.git('add', '-A', '-N', '.', env=env)
        settings = ['-c', f'core.whitespace={whitespace}'] if whitespace else []
        ok = self.run(['git', *settings, 'diff', '--check', self.base], 'diff-check.txt', env=env)
        shutil.rmtree(tmp)
        self.r['diff_check_clean'] = ok

    def scope_check(self, allowed):
        """Changed files outside the allowed regex: out-of-scope.txt and out_of_scope_files."""
        self.r['out_of_scope_files'] = self.write('out-of-scope.txt', grep_v(allowed, self.changed))

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

    def wrote_tests(self, prefix):
        """agent_wrote_tests: the agent added or changed something under the test folder."""
        self.r['agent_wrote_tests'] = any(f.startswith(prefix) for f in self.changed)

    # Builds and tests

    def command(self, key, cmd, cwd=None, log=None, summary=None, timeout=None):
        """Runs a program (a build, a test suite) and sets key to whether it exited 0. Output goes to
        <out>/<key>.log; with summary (a regex), the last matching line goes to key_summary."""
        log = log or f'{key}.log'
        self.r[key] = self.run(cmd, log, cwd=cwd, timeout=timeout)
        if summary:
            self.r[f'{key}_summary'] = last_match(summary, read_text(os.path.join(self.out, log)))

    def junit(self, key, test_files, classes, classpath, log=None, summary_key=None, java_props=()):
        """Compiles test files with RunTests against a classpath and runs the given classes. Sets key and
        key_summary (RunTests' 'TESTS found=.. ok=.. fail=..' line)."""
        log = log or f'{key}.log'
        tmp = tempfile.mkdtemp()
        try:
            ok = self.run([self.java_tool('javac'), '-cp', classpath, '-d', tmp, RUN_TESTS, *test_files], log) and \
                self.run([self.java_tool('java'), *java_props, '-cp', f'{tmp}:{classpath}', 'RunTests', *classes], log,
                         append=True)
        finally:
            shutil.rmtree(tmp, ignore_errors=True)
        self.r[key] = ok
        self.r[summary_key or f'{key}_summary'] = tests_summary(read_text(os.path.join(self.out, log)))

    def emit(self):
        """Writes auto.json, keys sorted, and returns its text."""
        text = json.dumps(dict(sorted(self.r.items())), indent=2) + '\n'
        write_text(os.path.join(self.out, 'auto.json'), text)
        return text
