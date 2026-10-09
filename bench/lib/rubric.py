"""Gates and automatic score suggestions, from a run's grade/auto.json.

Automatic scores are suggestions. A human value in grade/human.json always wins. Verification and
merge-worthiness have no automatic suggestion: they need the transcript and judgement.
This file has the defaults; a task's grade.py can override parts of them (see suggest and gates).
"""
from taskset import task_module

DIMENSIONS = ['correctness', 'procedure', 'reuse', 'scope', 'verification', 'merge']

# Procedure: auto.json keys whose value means a rule was broken
RULE_BREAKS = {
    'diff_check_clean': lambda v: v is False,
    'comment_dash_lines': lambda v: isinstance(v, int) and v > 0,
    'copyright_bumped': lambda v: v is False,
    'crlf_kept': lambda v: v is False,
    'numbered_identifiers': lambda v: isinstance(v, int) and v > 0,
    'generated_in_sync': lambda v: v is False,
    'header_on_new_tests': lambda v: v is False,
    'uses_junit5': lambda v: v is False,
    'class_modified': lambda v: v is True,
    'pushed_to_protected_branch': lambda v: v not in (False, 0, None, '', '""'),
    'deploy_order_ok': lambda v: v is False,
}

BUILD_KEYS = ['compile_ok', 'pc_compile_ok', 'regen_ok', 'tsc_ok', 'ng_build_ok', 'ts_tests_ok']


def band(n, bands):
    """bands: list of (minimum, score), highest minimum first."""
    for minimum, score in bands:
        if n >= minimum:
            return score
    return 0


def misses(*conds):
    """3 minus the number of conditions that do not hold."""
    return 3 - sum(1 for c in conds if not c)


def _procedure(a):
    broken = [k for k, bad in RULE_BREAKS.items() if k in a and bad(a[k])]
    return max(0, 3 - len(broken)), broken


def _scope(a):
    n = a.get('out_of_scope_files')
    if n is None:
        return None
    return band(-n, [(0, 3), (-2, 2), (-5, 1)])


def hidden_test_ok(a):
    return a.get('hidden_test_ok') is True


def gates(task, a):
    """builds: every build key present is true. behaviour: the task's behaviour(auto), by default the hidden test."""
    hooks = task_module(task)
    builds = all(a[k] is True for k in BUILD_KEYS if k in a)
    behaviour = hooks.behaviour(a) if hasattr(hooks, 'behaviour') else hidden_test_ok(a)
    return {'builds': builds, 'behaviour': behaviour}


def suggest(task, a):
    """Automatic suggestion per dimension (None = human only) and the rules seen broken.
    Defaults: correctness 3 when the hidden test passes, else 0; procedure 3 minus broken rules; scope from the
    out of scope file count; no reuse suggestion. A task's grade.py can replace correctness, reuse and procedure."""
    hooks = task_module(task)
    procedure, broken = _procedure(a)
    if hasattr(hooks, 'procedure'):
        procedure, broken = hooks.procedure(a, procedure, broken)
    return {
        'correctness': hooks.correctness(a) if hasattr(hooks, 'correctness') else (3 if hidden_test_ok(a) else 0),
        'procedure': procedure,
        'reuse': hooks.reuse(a) if hasattr(hooks, 'reuse') else None,
        'scope': _scope(a),
        'verification': None,
        'merge': None,
    }, broken
