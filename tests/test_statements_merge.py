from __future__ import annotations

from shortlist.data.models import (
    SourceResult, Statements, TickerSnapshot, merge_snapshots,
)
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


# --- the merger -----------------------------------------------------------

def _sr(source: str, st: Statements) -> SourceResult:
    return SourceResult(source=source, partial=TickerSnapshot(ticker="X", statements=st))


def _fmp_st() -> Statements:
    """An FMP-shaped Statements: 5 fiscal years, no EDGAR-only fields."""
    return Statements(
        fiscal_years=[2025, 2024, 2023, 2022, 2021],
        revenue=[500.0, 450.0, 400.0, 350.0, 300.0],
        gross_profit=[250.0, 225.0, 200.0, 175.0, 150.0],
        net_income=[50.0, 45.0, 40.0, 35.0, 30.0],
        total_equity=[900.0, 850.0, 800.0, 750.0, 700.0],
    )


def _edgar_st(newest: int = 2025) -> Statements:
    """An EDGAR-shaped Statements: 3 fiscal years, EDGAR-only fields populated."""
    years = [newest, newest - 1, newest - 2]
    return Statements(
        fiscal_years=years,
        fiscal_period_end=[f"{y}-09-28" for y in years],
        revenue=[500.0, 450.0, 400.0],
        diluted_shares=[1102.5, 1050.0, 1000.0],
        diluted_eps=[4.5, 4.2, 4.0],
        total_assets=[3000.0, 2800.0, 2600.0],
        asset_growth=0.0714,
        accruals=-0.02,
        dividends_paid=15.0,
        repurchases=80.0,
        debt_repayments=30.0,
        debt_issuance=10.0,
    )


def _merged(priority=("fmp", "edgar")) -> TickerSnapshot:
    return merge_snapshots(
        "X", [_sr("fmp", _fmp_st()), _sr("edgar", _edgar_st())], priority=list(priority)
    )


def test_edgar_only_fields_survive_an_fmp_won_merge():
    st = _merged().statements
    # FMP keeps the spine: 5 years of revenue, untouched.
    assert st.fiscal_years == [2025, 2024, 2023, 2022, 2021]
    assert st.revenue == [500.0, 450.0, 400.0, 350.0, 300.0]
    assert st.gross_profit[0] == 250.0
    # EDGAR-only lists are recovered, year-joined onto the 5-year spine.
    assert st.diluted_shares == [1102.5, 1050.0, 1000.0, None, None]
    assert st.diluted_eps == [4.5, 4.2, 4.0, None, None]
    assert st.total_assets == [3000.0, 2800.0, 2600.0, None, None]
    assert st.fiscal_period_end == ["2025-09-28", "2024-09-28", "2023-09-28", None, None]


def test_latest_fy_scalars_copy_when_newest_years_agree():
    st = _merged().statements
    assert st.asset_growth == 0.0714
    assert st.accruals == -0.02
    assert st.dividends_paid == 15.0
    assert st.repurchases == 80.0
    assert st.debt_repayments == 30.0
    assert st.debt_issuance == 10.0


def test_latest_fy_scalars_abstain_when_the_donor_vintage_is_older():
    # EDGAR's newest FY is 2024, the spine's is 2025: a "latest FY" scalar would
    # describe a different year than the object's [0] row -> abstain.
    merged = merge_snapshots(
        "X", [_sr("fmp", _fmp_st()), _sr("edgar", _edgar_st(newest=2024))],
        priority=["fmp", "edgar"],
    )
    st = merged.statements
    assert st.asset_growth is None
    assert st.accruals is None
    assert st.repurchases is None
    # ...but the LIST fields still backfill on their matching years.
    assert st.diluted_shares == [None, 1102.5, 1050.0, 1000.0, None]


def test_provenance_lists_both_contributors_in_priority_order():
    assert _merged().provenance["statements"] == ["fmp", "edgar"]


def test_source_partials_are_never_mutated():
    fmp_sr, edgar_sr = _sr("fmp", _fmp_st()), _sr("edgar", _edgar_st())
    merge_snapshots("X", [fmp_sr, edgar_sr], priority=["fmp", "edgar"])
    # The winner is copied, not aliased: the source object is unchanged.
    assert fmp_sr.partial.statements == _fmp_st()
    assert fmp_sr.partial.statements.diluted_shares == []
    assert edgar_sr.partial.statements == _edgar_st()


def test_reverse_direction_fmp_backfills_gross_profit_when_edgar_wins():
    # The claim in sources/edgar.py's comment: "the merge layer fills them from
    # FMP when available." Now true.
    merged = merge_snapshots(
        "X", [_sr("fmp", _fmp_st()), _sr("edgar", _edgar_st())], priority=["edgar", "fmp"]
    )
    st = merged.statements
    assert st.fiscal_years == [2025, 2024, 2023]        # EDGAR spine
    assert st.gross_profit == [250.0, 225.0, 200.0]     # from FMP, year-joined
    assert st.total_equity == [900.0, 850.0, 800.0]
    assert st.diluted_shares == [1102.5, 1050.0, 1000.0]


def test_single_source_merge_is_unchanged():
    only_fmp = merge_snapshots("X", [_sr("fmp", _fmp_st())], priority=["fmp", "edgar"])
    assert only_fmp.statements == _fmp_st()
    assert only_fmp.provenance["statements"] == ["fmp"]
    only_edgar = merge_snapshots("X", [_sr("edgar", _edgar_st())], priority=["fmp", "edgar"])
    assert only_edgar.statements == _edgar_st()
    assert only_edgar.provenance["statements"] == ["edgar"]


def test_spine_without_a_year_key_disables_backfill():
    spine = Statements(revenue=[1.0, 2.0])          # no fiscal_years
    merged = merge_snapshots(
        "X", [_sr("fmp", spine), _sr("edgar", _edgar_st())], priority=["fmp", "edgar"]
    )
    assert merged.statements.diluted_shares == []   # no join key -> no guess
    assert merged.provenance["statements"] == ["fmp"]


def test_a_donor_with_duplicate_years_is_skipped_not_fatal():
    # finnhub-shaped junk donor with an ambiguous spine must not veto edgar.
    dupe = Statements(fiscal_years=[2025, 2025], diluted_shares=[1.0, 2.0])
    merged = merge_snapshots(
        "X",
        [_sr("fmp", _fmp_st()), _sr("finnhub", dupe), _sr("edgar", _edgar_st())],
        priority=["fmp", "finnhub", "edgar"],
    )
    assert merged.statements.diluted_shares == [1102.5, 1050.0, 1000.0, None, None]
    assert merged.provenance["statements"] == ["fmp", "edgar"]


def test_a_donor_year_outside_the_spine_is_dropped_not_shifted():
    # EDGAR has a 2026 fiscal year the FMP spine doesn't carry. That row must be
    # DROPPED, not shifted onto 2025 (which is what a positional copy would do).
    merged = merge_snapshots(
        "X", [_sr("fmp", _fmp_st()), _sr("edgar", _edgar_st(newest=2026))],
        priority=["fmp", "edgar"],
    )
    st = merged.statements
    assert st.diluted_shares == [1050.0, 1000.0, None, None, None]  # 2025, 2024 only
    assert 1102.5 not in st.diluted_shares                          # the 2026 row is gone
    assert st.asset_growth is None            # newest years disagree -> scalars abstain


def test_all_empty_statements_merge_to_none():
    merged = merge_snapshots(
        "X", [_sr("fmp", Statements()), _sr("edgar", Statements())],
        priority=["fmp", "edgar"],
    )
    assert merged.statements is None
    assert "statements" not in merged.provenance


def test_every_statements_field_is_covered_by_the_merger():
    # Guard: a NEW Statements field must land in one of the two buckets (list
    # series or latest-FY scalar) or it will be silently dropped on merge.
    from dataclasses import fields

    from shortlist.data.models import _STATEMENTS_LATEST_FY_SCALARS
    blank = Statements()
    lists = {f.name for f in fields(blank) if isinstance(getattr(blank, f.name), list)}
    assert lists | set(_STATEMENTS_LATEST_FY_SCALARS) == {f.name for f in fields(blank)}


# --- end-to-end: the live behaviour this fix restores ---------------------

def _shipped_config() -> dict:
    import yaml
    from pathlib import Path
    return yaml.safe_load((Path(__file__).parents[1] / "config.yaml").read_text())


def test_dilution_flag_can_now_fire_on_an_fmp_covered_name():
    from shortlist.data.bridge import snapshot_to_metrics
    from shortlist.scoring import score

    cfg = _shipped_config()
    # Self-documenting precondition: the flag ships ON, and the fixture's 5%/yr
    # issuance must clear whatever floor is configured.
    assert cfg["flags"]["dilution"]["min_share_cagr"] <= 0.05

    snap = _merged()                       # fmp wins the spine, edgar backfills
    m = snapshot_to_metrics(snap)
    # cagr over [1102.5, 1050.0, 1000.0] newest-first = 5%/yr net issuance.
    assert m.share_count_cagr is not None
    assert abs(m.share_count_cagr - 0.05) < 1e-9

    assert "dilution" in score(m, cfg).flags


def test_dilution_flag_could_not_fire_before_the_fix():
    # The same FMP snapshot WITHOUT edgar in the chain: share_count_cagr stays
    # None, so the flag is structurally unreachable. This is what every
    # non-402 name looked like before this change.
    from shortlist.data.bridge import snapshot_to_metrics
    from shortlist.scoring import score

    snap = merge_snapshots("X", [_sr("fmp", _fmp_st())], priority=["fmp", "edgar"])
    m = snapshot_to_metrics(snap)
    assert m.share_count_cagr is None

    assert "dilution" not in score(m, _shipped_config()).flags


def test_measurement_inputs_reach_the_metrics():
    # The §3/§5 measurement inputs the accumulation store was persisting empty.
    from shortlist.data.bridge import snapshot_to_metrics

    m = snapshot_to_metrics(_merged())
    assert m.asset_growth == 0.0714
    assert m.accruals == -0.02
    assert m.eps_cagr_ps is not None       # from the recovered diluted_eps
