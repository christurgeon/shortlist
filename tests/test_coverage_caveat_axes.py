"""Pin the hand-synced axis list in the dependency-free coverage_caveat leaf to
coverage.py's _SUBSCORE_FIELDS — they must stay equal even though coverage_caveat
intentionally does NOT import coverage at runtime."""
from shortlist.coverage import _SUBSCORE_FIELDS
from shortlist.research.coverage_caveat import _AXIS


def test_axis_matches_coverage_subscore_fields():
    assert set(_AXIS) == set(_SUBSCORE_FIELDS)
