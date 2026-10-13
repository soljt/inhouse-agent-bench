"""Gates, the scoring scale and automatic score suggestions, from a run's grade/auto.json.

Automatic scores are suggestions. A human value in grade/human.json always wins. Verification and
merge-worthiness have no automatic suggestion: they need the transcript and judgement.
This file has the defaults; a task's grade.py (its hooks module) can override parts of them, see suggest and gates.

Scores run from 1 to 10. The hooks (correctness, reuse, procedure) and the helpers band and misses work on a
coarse 0 to 3 scale; suggest maps that onto 1 to 10 (0, 1, 2, 3 become 1, 4, 7, 10), so the suggestions land in
the bands DIMENSION_HELP describes.
"""

DIMENSIONS = ['correctness', 'procedure', 'reuse', 'scope', 'verification', 'merge']
SCALE = 10

# What each dimension asks, and what a score in each band looks like. Shown on the review page.
DIMENSION_HELP = {
    'correctness': {
        'question': 'Does the change do what the task asks, for the right reason?',
        'bands': ['Fixes the cause, edge cases handled', 'Fixes it with a minor gap',
                  'Fixes the symptom only, or a real gap', 'Wrong or no fix'],
    },
    'procedure': {
        'question': "Did it follow the repository's way of working: edit sources not generated files, follow "
                    'the skills and AGENTS.md, keep line endings and headers?',
        'bands': ['Every rule followed', 'One slip, corrected by the agent', 'A rule broken, partly repaired',
                  'A rule broken and left'],
    },
    'reuse': {
        'question': 'Does it reuse the constants, helpers and mechanisms that already exist, with one source of truth?',
        'bands': ['Reuses what exists, one source of truth', 'Small duplication', 'The same literal or logic in two places',
                  'A new parallel mechanism next to the existing one'],
    },
    'scope': {
        'question': 'Did it change only what the task needs?',
        'bands': ['Only what the task needs', 'One or two harmless extra edits', 'Drive-by refactors',
                  'Unrelated changes or reformatting'],
    },
    'verification': {
        'question': "Did it check its own work (the right builds and tests), and is its final report true? Read the "
                    'commands it ran next to what its final message claims.',
        'bands': ['Ran the right builds and tests, reports truthfully, names what it could not do',
                  'Verified most of it, reports accurately', 'Little verification, or vague claims',
                  'Claims checks it did not run'],
    },
    'merge': {
        'question': 'As the reviewer of this merge request, would you merge it?',
        'bands': ['Merge as is', 'Merge after small review comments', 'Needs rework', 'Would not merge'],
    },
}
# The score ranges the four bands above stand for, best first
BANDS = ['9 to 10', '6 to 8', '3 to 5', '1 to 2']

# Defaults for a task set whose module (taskset.toml: module) does not define RULE_BREAKS or BUILD_KEYS.
# Procedure: auto.json keys whose value means a rule was broken
RULE_BREAKS = {
    'diff_check_clean': lambda v: v is False,
}

# The build gate: every one of these that a task reports must be true
BUILD_KEYS = ['compile_ok', 'build_ok']

# What the generic checks (checks.py) put in auto.json: key -> (good value, what it measures). The good value is
# True or False, 0 (any count above it is bad), 'high' or 'low' (more or less is better, no fixed line) or None
# (information only). A task set's module and a task's grade.py add their own keys with an AUTO_KEYS dict of
# the same shape (see taskset.auto_keys). A <key>_summary key is described by its key.
AUTO_KEYS = {
    'changed_files': (None, 'Files the agent added, changed or deleted (changed-files.txt)'),
    'out_of_scope_files': (0, "Changed files outside the paths the task needs (out-of-scope.txt); feeds Scope"),
    'diff_check_clean': (True, 'git diff --check found no whitespace errors (trailing spaces, mixed indentation)'),
    'hidden_test_ok': (True, "The task's hidden tests pass on the agent's code: the behaviour gate"),
    'agent_wrote_tests': ('high', 'The agent added or changed tests (a plus, not required)'),
    'compile_ok': (True, 'The code compiles after the run: the build gate'),
    'build_ok': (True, 'The build succeeds after the run: the build gate'),
}


def to_scale(v):
    """A 0 to 3 hook value on the 1 to 10 scale; None stays None."""
    if v is None:
        return None
    return 1 + 3 * max(0, min(3, v))


def verdict(value, good):
    """'ok' or 'bad' for an auto.json value against its good value, None when there is no fixed line."""
    if good is True or good is False:
        if isinstance(value, bool):
            return 'ok' if value is good else 'bad'
        return None
    if good == 0 and isinstance(value, int) and not isinstance(value, bool):
        return 'ok' if value == 0 else 'bad'
    return None


def band(n, bands):
    """bands: list of (minimum, score), highest minimum first."""
    for minimum, score in bands:
        if n >= minimum:
            return score
    return 0


def misses(*conds):
    """3 minus the number of conditions that do not hold."""
    return 3 - sum(1 for c in conds if not c)


def _procedure(a, rule_breaks):
    broken = [k for k, bad in rule_breaks.items() if k in a and bad(a[k])]
    return max(0, 3 - len(broken)), broken


def _scope(a):
    n = a.get('out_of_scope_files')
    if n is None:
        return None
    return band(-n, [(0, 3), (-2, 2), (-5, 1)])


def hidden_test_ok(a):
    return a.get('hidden_test_ok') is True


def gates(hooks, a, build_keys=None):
    """builds: every build key present is true. behaviour: the task's behaviour(auto), by default the hidden test."""
    builds = all(a[k] is True for k in (build_keys or BUILD_KEYS) if k in a)
    behaviour = hooks.behaviour(a) if hasattr(hooks, 'behaviour') else hidden_test_ok(a)
    return {'builds': builds, 'behaviour': behaviour}


def suggest(hooks, a, rule_breaks=None):
    """Automatic suggestion per dimension on the 1 to 10 scale (None = human only) and the rules seen broken.
    Defaults (0 to 3, then scaled): correctness 3 when the hidden test passes, else 0; procedure 3 minus broken
    rules; scope from the out of scope file count; no reuse suggestion. A task's grade.py can replace
    correctness, reuse and procedure."""
    procedure, broken = _procedure(a, rule_breaks or RULE_BREAKS)
    if hasattr(hooks, 'procedure'):
        procedure, broken = hooks.procedure(a, procedure, broken)
    coarse = {
        'correctness': hooks.correctness(a) if hasattr(hooks, 'correctness') else (3 if hidden_test_ok(a) else 0),
        'procedure': procedure,
        'reuse': hooks.reuse(a) if hasattr(hooks, 'reuse') else None,
        'scope': _scope(a),
        'verification': None,
        'merge': None,
    }
    return {d: to_scale(v) for d, v in coarse.items()}, broken
