from shortlist._util import first, from_millions, pct


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


def test_pct_converts_percentage_to_fraction():
    assert pct(42.0) == 0.42
    assert pct(0) == 0.0


def test_pct_none_for_non_numeric():
    # The isinstance guard tolerates soft-failure payloads (strings/None) that
    # the old `x is not None` guard would have crashed on (TypeError).
    assert pct(None) is None
    assert pct("5.2") is None


def test_from_millions_scales_to_absolute_dollars():
    assert from_millions(2500.0) == 2.5e9
    assert from_millions(0) == 0.0


def test_from_millions_none_for_non_numeric():
    assert from_millions(None) is None
    assert from_millions("1000") is None
