"""Gates and automatic score suggestions per task, from a run's grade/auto.json.

Automatic scores are suggestions. A human value in grade/human.json always wins. Verification and
merge-worthiness have no automatic suggestion: they need the transcript and judgement.
"""

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


def _band(n, bands):
    """bands: list of (minimum, score), highest minimum first."""
    for minimum, score in bands:
        if n >= minimum:
            return score
    return 0


def _misses(*conds):
    return 3 - sum(1 for c in conds if not c)


def _correctness(task, a):
    tid = task.split('-')[0]
    ok = a.get('hidden_test_ok') is True
    if tid == 't5':
        return _band(a.get('mutants_killed', 0), [(6, 3), (4, 2), (2, 1)])
    if tid == 't12':
        return _band(a.get('defects_found', 0), [(7, 3), (5, 2), (3, 1)])
    if tid == 't2':
        keys = ['released_jar_has_constant', 'parent_manages_new_version', 'pc_parent_updated',
                'classpath_updated', 'commons_lib_only_new_jar']
        return max(1, 3 - sum(1 for k in keys if a.get(k) is not True))
    if not ok:
        return 0
    if tid in ('t1', 't1b'):
        return 3 if a.get('frontend_hardcoded_ts') is False else 2
    if tid == 't8':
        return 3 if a.get('edge_cases_ok') is True else 2
    if tid == 't9':
        return 3 if a.get('legacy_refs_ok') is True else 2
    return 3


def _reuse(task, a):
    tid = task.split('-')[0]
    if tid in ('t1', 't1b'):
        return max(1, _misses(a.get('frontend_hardcoded_ts') is False, a.get('edited_constants_xml') is True))
    if tid == 't4':
        return 3 if a.get('new_blank_helper') is False else 1
    if tid == 't12':
        # For a review: did it catch the DRY slip (D1) and the wrong reuse of an existing limit (D2)
        ids = str(a.get('defect_ids', '')).split(',')
        return 1 + ('D1' in ids) + ('D2' in ids)
    if tid == 't7':
        return 3 if a.get('uses_error_helper') is True else 2
    if tid in ('t8', 't13'):
        return max(0, _misses(a.get('limit_literal_lines', 9) <= 1, a.get('length_check_lines', 9) <= 1,
                              a.get('attribute_name_literals', 9) == 0))
    if tid == 't9':
        return _band(-a.get('magic_number_lines', 9), [(0, 3), (-2, 2), (-99, 1)])
    if tid == 't10':
        return max(1, _misses(a.get('english_in_show_error', 9) == 0, a.get('prefix_literals', 9) <= 1))
    return None


def _procedure(a):
    broken = [k for k, bad in RULE_BREAKS.items() if k in a and bad(a[k])]
    return max(0, 3 - len(broken)), broken


def _scope(a):
    n = a.get('out_of_scope_files')
    if n is None:
        return None
    return _band(-n, [(0, 3), (-2, 2), (-5, 1)])


def gates(task, a):
    tid = task.split('-')[0]
    builds = all(a[k] is True for k in BUILD_KEYS if k in a)
    if tid == 't5':
        behaviour = a.get('tests_pass_on_original') is True and a.get('tests_found', 0) > 0
    elif tid == 't2':
        behaviour = a.get('released_jar_has_constant') is True and a.get('parent_manages_new_version') is True
    elif tid == 't12':
        behaviour = a.get('review_file_present') is True
    else:
        behaviour = a.get('hidden_test_ok') is True
    return {'builds': builds, 'behaviour': behaviour}


def suggest(task, a):
    """Automatic suggestion per dimension (None = human only) and the rules seen broken."""
    tid = task.split('-')[0]
    procedure, broken = _procedure(a)
    if tid == 't12':
        # A review has no repository rules to break; the procedure is the requested findings format
        broken = ['review_format'] if a.get('defects_unparsed', 0) else []
        procedure = 3 if not broken else 2
    if tid == 't13':
        # Asking for the missing limit before editing is the point of the task; a barrage of questions is not
        if a.get('asked_before_editing') is not True:
            broken = broken + ['guessed_instead_of_asking']
            procedure = max(0, procedure - 2)
        if a.get('turn1_questions', 0) > 3:
            broken = broken + ['too_many_questions']
            procedure = max(0, procedure - 1)
    return {
        'correctness': _correctness(task, a),
        'procedure': procedure,
        'reuse': _reuse(task, a),
        'scope': _scope(a),
        'verification': None,
        'merge': None,
    }, broken
