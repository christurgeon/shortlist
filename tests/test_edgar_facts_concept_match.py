from __future__ import annotations

import pandas as pd

from shortlist.providers._edgar_facts import (
    _row_by_standard_concept,
    _series,
    extract_financials,
)

SHARES = "us-gaap_WeightedAverageNumberOfDilutedSharesOutstanding"
BASIC = "us-gaap_WeightedAverageNumberOfSharesOutstandingBasic"
ABSTRACT = "us-gaap_WeightedAverageNumberOfSharesOutstandingAbstract"
EPS = "us-gaap_EarningsPerShareDiluted"
EPS_CONTINUING = "us-gaap_IncomeLossFromContinuingOperationsPerDilutedShare"
REVENUE_SC, NI_SC = "Revenue", "NetIncomeLoss"

FY = ["2025-06-30 (FY)", "2024-06-30 (FY)", "2023-06-30 (FY)"]
NI = [90e9, 88e9, 72e9]


def _row(label, vals, concept=None, standard_concept=None, dimension=False, level=1):
    d = {"label": label, "concept": concept, "standard_concept": standard_concept,
         "dimension": dimension, "level": level}
    d.update(dict(zip(FY, vals, strict=True)))
    return d


def _income(rows: list[dict]) -> pd.DataFrame:
    base = [_row("Revenue", [200e9, 190e9, 180e9], standard_concept=REVENUE_SC),
            _row("Net income", NI, standard_concept=NI_SC)]
    return pd.DataFrame(base + rows)


_EMPTY = pd.DataFrame()


def _extract(income_rows, shares_scalar=7_000e6):
    return extract_financials(_income(income_rows), _EMPTY, _EMPTY,
                              shares_diluted=shares_scalar)


# --- root cause A: share-count label misses, concept hits -----------------

def test_msft_style_bare_diluted_label_recovers_shares():
    ef = _extract([
        _row("Diluted", [17.95, 13.64, 11.80], concept=EPS),
        _row("Weighted average shares outstanding:", [None, None, None], concept=ABSTRACT),
        _row("Basic", [7_430e6, 7_440e6, 7_450e6], concept=BASIC),
        _row("Diluted", [7_453e6, 7_465e6, 7_469e6], concept=SHARES),
    ])
    assert ef.diluted_shares == [7_453e6, 7_465e6, 7_469e6]


def test_ibm_style_assuming_dilution_label_recovers_shares():
    ef = _extract([_row("Assuming dilution (in shares)", [920e6, 925e6, 930e6], concept=SHARES)])
    assert ef.diluted_shares == [920e6, 925e6, 930e6]


def test_vz_style_label_without_the_word_diluted_recovers_shares():
    ef = _extract([_row("Weighted-average shares outstanding (in shares)",
                        [4.2e9, 4.2e9, 4.2e9], concept=SHARES)])
    assert ef.diluted_shares == [4.2e9, 4.2e9, 4.2e9]


def test_basic_share_row_is_never_used():
    ef = _extract([_row("Basic (in shares)", [1.0, 2.0, 3.0], concept=BASIC)])
    assert ef.diluted_shares == []


def test_dimensional_breakdown_rows_are_ignored():
    ef = _extract([
        _row("Diluted", [99.0, 99.0, 99.0], concept=SHARES, dimension=True),
        _row("Diluted", [7.0, 8.0, 9.0], concept=SHARES),
    ])
    assert ef.diluted_shares == [7.0, 8.0, 9.0]


def test_nested_child_row_loses_to_the_min_level_row():
    # Mirrors the MSFT OCF failure that motivated _row_by_standard_concept's
    # min-level tie-break: iloc[0] would grab the level-4 child.
    ef = _extract([
        _row("Diluted (child)", [1.0, 2.0, 3.0], concept=SHARES, level=4),
        _row("Diluted", [7.0, 8.0, 9.0], concept=SHARES, level=2),
    ])
    assert ef.diluted_shares == [7.0, 8.0, 9.0]


# --- [R2] Critical 1: value-aware fallback (regression guards) ------------

def test_sparse_concept_row_does_not_shadow_a_working_label_row():
    # _series is all-or-nothing. A concept row with a NaN must NOT beat a
    # complete label-matched row, or a populated series silently becomes [].
    ef = _extract([
        _row("Diluted", [7_453e6, None, 7_469e6], concept=SHARES),
        _row("Weighted average diluted shares", [1e9, 2e9, 3e9]),
    ])
    assert ef.diluted_shares == [1e9, 2e9, 3e9]


def test_all_nan_concept_row_does_not_shadow_a_working_label_row():
    ef = _extract([
        _row("Weighted average shares outstanding:", [None, None, None], concept=SHARES),
        _row("Weighted average diluted shares", [1e9, 2e9, 3e9]),
    ])
    assert ef.diluted_shares == [1e9, 2e9, 3e9]


def test_label_scan_still_works_with_no_concept_column():
    df = pd.DataFrame([
        {"label": "Revenue", "standard_concept": REVENUE_SC, **dict(zip(FY, [1.0, 1.0, 1.0], strict=True))},
        {"label": "Weighted average diluted shares", **dict(zip(FY, [1e9, 2e9, 3e9], strict=True))},
    ])
    ef = extract_financials(df, _EMPTY, _EMPTY, shares_diluted=None)
    assert ef.diluted_shares == [1e9, 2e9, 3e9]


def test_aapl_style_label_still_works_no_regression():
    ef = _extract([_row("Diluted (in shares)", [15.0e9, 15.4e9, 15.8e9], concept=SHARES)])
    assert ef.diluted_shares == [15.0e9, 15.4e9, 15.8e9]


# --- [R2] Critical 3: the EPS provenance flip, pinned ---------------------

def test_bare_diluted_eps_label_uses_reported_values_not_the_computed_fallback():
    scalar = 7_000e6
    ef = _extract([
        _row("Diluted", [17.95, 13.64, 11.80], concept=EPS),
        _row("Diluted", [7_453e6, 7_465e6, 7_469e6], concept=SHARES),
    ], shares_scalar=scalar)
    assert ef.diluted_eps == [17.95, 13.64, 11.80]          # as-reported
    assert ef.diluted_eps != [ni / scalar for ni in NI]     # NOT the fallback


def test_computed_fallback_still_fires_when_no_eps_row_exists_at_all():
    scalar = 7_000e6
    ef = _extract([], shares_scalar=scalar)
    assert ef.diluted_eps == [ni / scalar for ni in NI]


def test_reported_eps_label_path_still_works():
    ef = _extract([_row("Diluted (in dollars per share)", [7.46, 6.08, 6.13], concept=EPS)])
    assert ef.diluted_eps == [7.46, 6.08, 6.13]


# --- [R2] Important 6: discontinued-operations row ordering ---------------

def test_concept_match_beats_a_competing_label_match_on_continuing_ops():
    # Real JNJ/QCOM shape (docs/audits/2026-07-31-edgar-concept-match.md, class E):
    # BOTH rows are labelled IDENTICALLY "Diluted (in dollars per share)" -- only the
    # `concept` column distinguishes the continuing-ops row from the total. A label
    # scan can't tell them apart at all (it would return whichever row comes first);
    # only matching `concept` first picks the true total. Labelling the continuing-ops
    # row "Continuing operations" (as an earlier revision of this test did) is a
    # vacuous pin: that string never matched the old "diluted" + "per share" label
    # regex either, so the test passed even against a label-first implementation.
    ef = _extract([
        _row("Diluted (in dollars per share)", [6.0, 5.0, 4.0], concept=EPS_CONTINUING),
        _row("Diluted (in dollars per share)", [5.0, 4.0, 3.0], concept=EPS),
    ])
    assert ef.diluted_eps == [5.0, 4.0, 3.0]


# --- _row_by_standard_concept: duplicated-index crash ----------------------

def test_row_by_standard_concept_survives_a_duplicated_index_with_the_min_level_row_not_first():
    # Real edgartools frames can carry a duplicated pandas index (measured: index
    # [7, 7], levels [4, 2]). `.loc[lvl.idxmin()]` on a duplicated index returns
    # EVERY row sharing that label as a DataFrame, not a Series -- `_series()` then
    # raises `ValueError: truth value of a Series is ambiguous` on `row.get(col)`,
    # which EdgarSource's failure isolation turns into a silent total loss of the
    # ticker's statements. The min-level row (level 2, value 7.0) is placed SECOND
    # here specifically so a `.loc`-based fix can't pass by accident.
    df = pd.DataFrame(
        [
            {"standard_concept": "NetCashFromOperatingActivities", "level": 4,
             "2025-06-30 (FY)": 1.0},
            {"standard_concept": "NetCashFromOperatingActivities", "level": 2,
             "2025-06-30 (FY)": 7.0},
        ],
        index=[7, 7],
    )
    row = _row_by_standard_concept(df, "NetCashFromOperatingActivities")
    assert row is not None
    assert isinstance(row, pd.Series)
    assert row["2025-06-30 (FY)"] == 7.0
    assert _series(row, [("2025-06-30", "2025-06-30 (FY)")]) == [7.0]
