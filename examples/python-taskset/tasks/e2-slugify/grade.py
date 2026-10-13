"""E2 checks: slugify, reusing the package's whitespace helper."""


def grade(g):
    g.record_diff(); g.diff_check()
    g.scope_check(r'^(textstats|tests)/')
    g.pytest('tests_ok', ['tests'])
    g.pytest('hidden_test_ok', [g.hidden('test_slugify_hidden.py')])
    g.wrote_tests('tests/')
    # AGENTS.md: reuse textstats/text.py for whitespace
    g.r['reuses_normalize_whitespace'] = any('normalize_whitespace' in l for l in g.added_lines(r'textstats/'))


# How the checks map to the rubric (defaults in bench/lib/rubric.py)

AUTO_KEYS = {
    'reuses_normalize_whitespace': (True, 'Added code in textstats/ calls normalize_whitespace; feeds Reuse'),
}


def reuse(a):
    return 3 if a.get('reuses_normalize_whitespace') is True else 1
