import pytest
from text_stats import top_words, word_counts


def words(result):
    """The request doesn't say whether top_words returns words or (word, count) pairs: accept both."""
    return [w if isinstance(w, str) else w[0] for w in result]


def test_counts_lowercase_and_punctuation():
    assert word_counts("Hello, hello! World.") == {"hello": 2, "world": 1}


def test_counts_empty():
    assert word_counts("") == {}


def test_top_words_by_count():
    assert words(top_words("b a c a b a", 2)) == ["a", "b"]


def test_top_words_ties_alphabetical():
    assert words(top_words("pear apple fig", 3)) == ["apple", "fig", "pear"]


def test_top_words_case_insensitive():
    assert words(top_words("A a B", 1)) == ["a"]


@pytest.mark.parametrize("n", [0, -1])
def test_top_words_invalid_n(n):
    with pytest.raises(ValueError):
        top_words("a b", n)
