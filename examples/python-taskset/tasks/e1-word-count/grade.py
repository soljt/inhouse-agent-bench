"""E1 checks: word_count counts words separated by any whitespace."""


def grade(g):
    g.record_diff(); g.diff_check()
    g.scope_check(r'^(textstats|tests)/')
    # Build gate: the existing tests still pass (they include the agent's own)
    g.pytest('tests_ok', ['tests'])
    g.pytest('hidden_test_ok', [g.hidden('test_word_count_hidden.py')])
    g.wrote_tests('tests/')
    # AGENTS.md: reuse textstats/text.py instead of new whitespace handling
    g.count_added('new_regexes', r're\.(sub|split|findall|compile)\(', r'textstats/')


# How the checks map to the rubric (defaults in bench/lib/rubric.py)

AUTO_KEYS = {
    'new_regexes': (0, 'New re.sub/split/findall/compile calls in textstats/ (AGENTS.md: reuse text.py); feeds Reuse'),
}

RUBRIC_NOTES = {
    'reuse': 'str.split() or normalize_whitespace, not a new regex.',
}


def reuse(a):
    """str.split() or normalize_whitespace, not a new regex."""
    return 3 if a.get('new_regexes', 9) == 0 else 1
