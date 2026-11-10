import pytest
from lru import LRUCache


def test_get_missing_is_none():
    assert LRUCache(2).get("x") is None


def test_put_get():
    c = LRUCache(2)
    c.put("a", 1)
    assert c.get("a") == 1


def test_evicts_least_recently_used():
    c = LRUCache(2)
    c.put("a", 1)
    c.put("b", 2)
    c.put("c", 3)
    assert c.get("a") is None and c.get("b") == 2 and c.get("c") == 3


def test_get_counts_as_use():
    c = LRUCache(2)
    c.put("a", 1)
    c.put("b", 2)
    c.get("a")
    c.put("c", 3)
    assert c.get("b") is None and c.get("a") == 1


def test_put_existing_updates_value():
    c = LRUCache(2)
    c.put("a", 1)
    c.put("a", 9)
    assert c.get("a") == 9


def test_capacity_one():
    c = LRUCache(1)
    c.put("a", 1)
    c.put("b", 2)
    assert c.get("a") is None and c.get("b") == 2


@pytest.mark.parametrize("cap", [0, -1])
def test_invalid_capacity(cap):
    with pytest.raises(ValueError):
        LRUCache(cap)
