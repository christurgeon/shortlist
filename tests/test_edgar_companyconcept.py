"""Task 1: pure companyconcept aggregator (`diluted_shares_from_concept`).
Task 2: the mockable network seam + fallback wiring on EdgarSource, isolated
from the rest of Statements construction (docs/PLAN_EDGAR_ROOT_CAUSE_B.md)."""
from __future__ import annotations

from shortlist.providers._edgar_facts import diluted_shares_from_concept

SPINE = ["2025-12-31", "2024-12-31", "2023-12-31"]


def _row(end, val, *, start=None, form="10-K", filed="2026-02-17"):
    """A single companyconcept 'shares' unit row. Default start is one 10-K
    annual duration (365d) before `end`."""
    if start is None:
        y, m, d = (int(x) for x in end.split("-"))
        start = f"{y - 1}-{m:02d}-{d:02d}"
    return {"start": start, "end": end, "val": val, "form": form,
            "fy": y_from(end), "fp": "FY", "filed": filed, "accn": "0001-25-000001"}


def y_from(end: str) -> int:
    return int(end[:4])


def _payload(rows: list[dict]) -> dict:
    return {"units": {"shares": rows}}


# --- happy path -------------------------------------------------------------

def test_happy_path_three_annual_10k_entries_match_spine_order():
    payload = _payload([
        _row("2025-12-31", 643_000_000.0),
        _row("2024-12-31", 655_000_000.0),
        _row("2023-12-31", 668_000_000.0),
    ])
    assert diluted_shares_from_concept(payload, SPINE) == [
        643_000_000.0, 655_000_000.0, 668_000_000.0]


# --- restatement dedup -------------------------------------------------------

def test_restatement_dedup_prefers_later_filed():
    payload = _payload([
        _row("2025-12-31", 643_000_000.0),
        _row("2024-12-31", 655_000_000.0, filed="2025-02-15"),      # original filing
        _row("2024-12-31", 654_500_000.0, filed="2026-02-17"),      # restated, later filed
        _row("2023-12-31", 668_000_000.0),
    ])
    out = diluted_shares_from_concept(payload, SPINE)
    assert out == [643_000_000.0, 654_500_000.0, 668_000_000.0]


def test_restatement_dedup_ties_keep_the_last_seen_via_gte():
    # Same `filed` twice for the same `end`: the >= comparison means the later
    # row in iteration order wins (not the first).
    payload = _payload([
        _row("2025-12-31", 111.0, filed="2026-01-01"),
        _row("2025-12-31", 222.0, filed="2026-01-01"),
        _row("2024-12-31", 655_000_000.0),
        _row("2023-12-31", 668_000_000.0),
    ])
    out = diluted_shares_from_concept(payload, SPINE)
    assert out[0] == 222.0


# --- duration guard -----------------------------------------------------------

def test_quarterly_entry_is_ignored_even_if_filed_later():
    payload = _payload([
        _row("2025-12-31", 643_000_000.0),
        # A quarterly fact sharing the same `end` as a spine year, filed AFTER
        # the annual row, with a deliberately wrong value — must not win.
        _row("2024-12-31", 999_999_999.0, start="2024-10-01", filed="2027-01-01"),
        _row("2024-12-31", 655_000_000.0),
        _row("2023-12-31", 668_000_000.0),
    ])
    out = diluted_shares_from_concept(payload, SPINE)
    assert out == [643_000_000.0, 655_000_000.0, 668_000_000.0]


def test_quarterly_only_year_leaves_that_year_uncovered_so_result_abstains():
    payload = _payload([
        _row("2025-12-31", 643_000_000.0),
        _row("2024-12-31", 999_999_999.0, start="2024-10-01"),  # quarterly only
        _row("2023-12-31", 668_000_000.0),
    ])
    assert diluted_shares_from_concept(payload, SPINE) == []


# --- partial coverage abstains -------------------------------------------------

def test_partial_coverage_two_of_three_years_abstains():
    payload = _payload([
        _row("2025-12-31", 643_000_000.0),
        _row("2024-12-31", 655_000_000.0),
        # 2023-12-31 missing entirely
    ])
    assert diluted_shares_from_concept(payload, SPINE) == []


def test_spine_year_entirely_absent_from_payload_abstains():
    # Payload only ever mentions fiscal years outside the requested spine.
    payload = _payload([
        _row("2022-12-31", 700_000_000.0),
        _row("2021-12-31", 710_000_000.0),
        _row("2020-12-31", 720_000_000.0),
    ])
    assert diluted_shares_from_concept(payload, SPINE) == []


# --- form filtering -----------------------------------------------------------

def test_non_10k_forms_are_ignored():
    payload = _payload([
        _row("2025-12-31", 643_000_000.0),
        _row("2024-12-31", 111.0, form="8-K", filed="2026-03-01"),  # would win dedup if counted
        _row("2024-12-31", 655_000_000.0),
        _row("2023-12-31", 668_000_000.0),
    ])
    out = diluted_shares_from_concept(payload, SPINE)
    assert out == [643_000_000.0, 655_000_000.0, 668_000_000.0]


def test_only_non_10k_forms_present_abstains():
    payload = _payload([
        _row("2025-12-31", 643_000_000.0, form="10-K/A"),
        _row("2024-12-31", 655_000_000.0, form="8-K"),
        _row("2023-12-31", 668_000_000.0, form="10-K"),
    ])
    assert diluted_shares_from_concept(payload, SPINE) == []


# --- malformed / empty input never raises --------------------------------------

def test_empty_payload_abstains():
    assert diluted_shares_from_concept({}, SPINE) == []


def test_none_payload_abstains():
    assert diluted_shares_from_concept(None, SPINE) == []


def test_missing_units_key_abstains():
    assert diluted_shares_from_concept({"other": 1}, SPINE) == []


def test_units_present_but_shares_missing_abstains():
    assert diluted_shares_from_concept({"units": {"usd": []}}, SPINE) == []


def test_units_shares_wrong_type_abstains():
    assert diluted_shares_from_concept({"units": {"shares": "not-a-list"}}, SPINE) == []


def test_malformed_row_entries_are_skipped_not_raised():
    payload = _payload([
        "not-a-dict",
        {"form": "10-K"},                      # missing start/end/val
        {"form": "10-K", "start": "bad", "end": "2025-12-31", "val": 1.0, "filed": "x"},
        _row("2025-12-31", 643_000_000.0),
        _row("2024-12-31", 655_000_000.0),
        _row("2023-12-31", 668_000_000.0),
    ])
    out = diluted_shares_from_concept(payload, SPINE)
    assert out == [643_000_000.0, 655_000_000.0, 668_000_000.0]


def test_empty_fiscal_period_end_abstains():
    payload = _payload([_row("2025-12-31", 643_000_000.0)])
    assert diluted_shares_from_concept(payload, []) == []
