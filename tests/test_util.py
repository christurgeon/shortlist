from shortlist._util import first


def test_first_unwraps_single_element_list():
    assert first([{"a": 1}]) == {"a": 1}


def test_first_returns_first_of_many():
    assert first([{"a": 1}, {"a": 2}]) == {"a": 1}


def test_first_passes_through_bare_dict():
    assert first({"a": 1}) == {"a": 1}


def test_first_none_for_empty_list():
    assert first([]) is None


def test_first_none_for_non_collection():
    assert first(None) is None
    assert first("oops") is None
    assert first(42) is None
