from __future__ import annotations

from shortlist.data.models import Statements
from shortlist.data.models import (
    _newest_year, _reindex_by_year, _usable_years,
)


# --- pure helpers ---------------------------------------------------------

def test_newest_year_ignores_none_holes():
    assert _newest_year([2025, 2024, None, 2022]) == 2025
    assert _newest_year([None, None]) is None
    assert _newest_year([]) is None


def test_usable_years_rejects_empty_and_duplicates():
    assert _usable_years(Statements(fiscal_years=[2025, 2024])) == [2025, 2024]
    assert _usable_years(Statements()) is None                       # no key
    assert _usable_years(Statements(fiscal_years=[2025, 2025])) is None  # ambiguous


def test_reindex_places_values_on_matching_years_and_pads_with_none():
    # Donor covers 3 of the spine's 5 years; the two oldest have no data.
    out = _reindex_by_year(
        donor_years=[2025, 2024, 2023],
        donor_values=[15.1, 15.4, 15.8],
        spine_years=[2025, 2024, 2023, 2022, 2021],
    )
    assert out == [15.1, 15.4, 15.8, None, None]


def test_reindex_aligns_by_year_not_position():
    # The donor's newest year is OLDER than the spine's newest. A positional
    # copy would put 9.0 on 2025; the year join must leave 2025 empty.
    out = _reindex_by_year(
        donor_years=[2024, 2023],
        donor_values=[9.0, 8.0],
        spine_years=[2025, 2024, 2023],
    )
    assert out == [None, 9.0, 8.0]


def test_reindex_returns_empty_when_no_year_overlaps():
    out = _reindex_by_year([2019, 2018], [1.0, 2.0], [2025, 2024])
    assert out == []


def test_reindex_never_joins_on_a_none_year():
    # A None year is not a key: it must not match the donor's None-keyed row.
    out = _reindex_by_year([None, 2024], [99.0, 5.0], [None, 2024])
    assert out == [None, 5.0]


def test_reindex_tolerates_a_short_value_series():
    # Ragged input must not raise (mirrors _financial_series' tolerance).
    out = _reindex_by_year([2025, 2024, 2023], [1.0], [2025, 2024])
    assert out == [1.0, None]
