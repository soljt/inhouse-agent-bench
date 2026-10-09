from textstats import word_count


def test_double_spaces():
    assert word_count('hello  world') == 2


def test_tabs_and_newlines():
    assert word_count('one\ttwo\nthree\n') == 3


def test_only_whitespace_has_no_words():
    assert word_count('  \t\n ') == 0


def test_plain_text_unchanged():
    assert word_count('one two three') == 3
    assert word_count('') == 0
