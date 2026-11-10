import pytest
from text_utils import is_palindrome


@pytest.mark.parametrize("s", ["A man, a plan, a canal: Panama", "No 'x' in Nixon", "", "a", "12321", "Was it a car or a cat I saw?"])
def test_palindromes(s):
    assert is_palindrome(s) is True


@pytest.mark.parametrize("s", ["race a car", "ab", "1a2"])
def test_not_palindromes(s):
    assert is_palindrome(s) is False
