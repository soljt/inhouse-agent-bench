from textstats import slugify


def test_examples_from_the_request():
    assert slugify('  Hello,   World!  ') == 'hello-world'
    assert slugify('Top 10 Tips') == 'top-10-tips'


def test_punctuation_between_words():
    assert slugify('Rock & Roll') == 'rock-roll'
    assert slugify('a--b') == 'a-b'


def test_tabs_and_newlines():
    assert slugify('one\ttwo\nthree') == 'one-two-three'


def test_nothing_left():
    assert slugify('!!!') == ''
