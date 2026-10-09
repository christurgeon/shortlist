"""The `/deep` ROIC-persistence profile: every reason it is not shown, the point-in-time rule,
and the thirds. A small hand-made table, except where a test says it reads the real one."""
from datetime import date, timedelta

import pytest

from shortlist import durability_profile as dp
from shortlist.durability import compact_facts

CONFIG = {"sectors": {"buckets": [{"name": "financial", "sic_ranges": [[6000, 6799]]}]}}
TODAY = date(2026, 3, 15)


def _table(table_year=2025):
    universe = [0.02 * i for i in range(1, 11)]            # 0.02 .. 0.20; top-fifth floor 0.18
    years = range(table_year - 3, table_year + 1)
    return {"schema": 1, "table_year": table_year,
            "universe": {y: list(universe) for y in years}, "floors": dict.fromkeys(years, 0.18),
            "cohort": {"n": 9,
                       # oriented: minus the capital growth
                       "investment": [-0.50, -0.40, -0.30, -0.20, -0.10, 0.00, 0.05, 0.10, 0.20],
                       # oriented: minus the spread of the percentile rank
                       "stability": [-0.30, -0.25, -0.20, -0.15, -0.10, -0.08, -0.05, -0.02, -0.01]}}


def _raw(years, *, lag=50, revenue=5e8, assets=1000.0, form="10-K", debt=None, ends=None):
    """Raw company facts: `years` is {year of the fiscal year end: (operating income, equity)}.
    Dec year ends unless `ends` gives one. Each year is filed `lag` days after it ends."""
    tags = {t: [] for t in ("Revenues", "OperatingIncomeLoss", "StockholdersEquity", "Assets",
                            "LongTermDebt")}
    for y, (oi, eq) in years.items():
        end = date.fromisoformat((ends or {}).get(y, f"{y}-12-31"))
        filed = (end + timedelta(days=lag)).isoformat()
        dur = {"start": (end - timedelta(days=364)).isoformat(), "end": end.isoformat(),
               "filed": filed, "form": form}
        inst = {"end": end.isoformat(), "filed": filed, "form": form}
        if revenue is not None:
            tags["Revenues"].append({**dur, "val": revenue})
        tags["OperatingIncomeLoss"].append({**dur, "val": oi})
        tags["StockholdersEquity"].append({**inst, "val": eq})
        tags["Assets"].append({**inst, "val": assets})
        if debt is not None:
            tags["LongTermDebt"].append({**inst, "val": debt})
    return {"cik": 1, "facts": {"us-gaap": {t: {"units": {"USD": rows}} for t, rows in tags.items() if rows}}}


def _profile(raw, today=TODAY, table=None, sic="3674", **kw):
    table = _table() if table is None else table
    compacted = compact_facts(raw) or {"facts": {"us-gaap": {}}}
    return dp.profile(compacted, today, table, sic=sic, config=CONFIG, **kw)


# Four years of a ROIC near 30% (152 x 0.79 / 400) on capital that grew 0%, 0%, then +35%.
STEADY = {2022: (152.0, 400.0), 2023: (152.0, 400.0), 2024: (152.0, 400.0), 2025: (216.0, 540.0)}


def test_a_top_fifth_company_gets_its_thirds():
    prof, status, detail = _profile(_raw(STEADY))
    assert (status, detail) == (dp.SHOWN, {})
    assert (prof.period_end, prof.bucket, prof.reference_year) == ("2025-12-31", 2025, 2025)
    assert abs(prof.roic - 216.0 * 0.79 / 540.0) < 1e-12
    assert (prof.floor, prof.universe_n, prof.years_seen) == (0.18, 10, 4)
    assert abs(prof.capital_growth - 0.35) < 1e-12
    assert prof.investment_third == 0                   # +35%: among the fastest-growing
    assert prof.stability_spread == 0.0                 # above every firm in all four years
    assert prof.stability_third == 2
    assert prof.has_debt is False


def test_tagged_debt_is_part_of_capital_and_is_reported():
    prof, _, _ = _profile(_raw(STEADY, debt=100.0))
    assert prof.has_debt is True
    assert abs(prof.roic - 216.0 * 0.79 / 640.0) < 1e-12


def test_no_table_is_unavailable():
    assert dp.profile({}, TODAY, None, sic="3674", config=CONFIG) == (None, dp.UNAVAILABLE, {})


@pytest.mark.parametrize("sic", [None, "", "abc"])
def test_an_unknown_sic_is_not_let_through_the_sector_mask(sic):
    # NOT the study's rule: there an unknown SIC is not masked. Live, the SIC comes from one
    # fetch that can fail, and a bank with no SIC would get a section.
    assert _profile(_raw(STEADY), sic=sic) == (None, dp.SIC_UNKNOWN, {})


def test_a_masked_sector_is_not_covered():
    assert _profile(_raw(STEADY), sic="6021") == (None, dp.SECTOR_NOT_COVERED, {})
    assert _profile(_raw(STEADY), sic=6021)[1] == dp.SECTOR_NOT_COVERED


def test_a_foreign_filer_has_no_annual_facts():
    assert _profile(_raw(STEADY, form="20-F")) == (None, dp.NO_ANNUAL_FACTS, {})
    assert _profile({"facts": {"us-gaap": {}}}) == (None, dp.NO_ANNUAL_FACTS, {})


def test_a_company_that_stopped_filing_is_stale():
    old = {y - 2: v for y, v in STEADY.items()}             # latest year end 2023-12-31
    # 2023-12-31 + 120 days = 2024-04-29; one year after that the next year should be on file.
    assert _profile(_raw(old), today=date(2025, 4, 29))[1] != dp.STALE
    assert _profile(_raw(old), today=date(2025, 4, 30)) == (None, dp.STALE, {"period_end": "2023-12-31"})


def test_a_late_10k_is_outside_the_universe_as_in_the_study():
    # Filed 150 days after the year end: at the study's as-of date (120 days) it is not there.
    assert _profile(_raw(STEADY, lag=150), today=date(2026, 9, 1)) == (
        None, dp.LATEST_YEAR_UNUSABLE, {"period_end": "2025-12-31"})


def test_a_latest_year_without_equity_or_revenue_is_unusable():
    raw = _raw(STEADY)
    raw["facts"]["us-gaap"]["StockholdersEquity"]["units"]["USD"].pop()
    assert _profile(raw)[1] == dp.LATEST_YEAR_UNUSABLE
    raw = _raw(STEADY)
    raw["facts"]["us-gaap"]["Revenues"]["units"]["USD"].pop()
    assert _profile(raw)[1] == dp.LATEST_YEAR_UNUSABLE       # NOT "revenue under $100M"


def test_capital_under_a_tenth_of_assets_has_no_roic():
    low = {**STEADY, 2025: (190.0, 50.0)}
    assert _profile(_raw(low)) == (None, dp.LOW_CAPITAL, {"period_end": "2025-12-31"})
    assert _profile(_raw({**STEADY, 2025: (190.0, -20.0)}))[1] == dp.LOW_CAPITAL


def test_revenue_under_the_floor():
    assert _profile(_raw(STEADY, revenue=9.9e7))[1] == dp.REVENUE_BELOW_FLOOR
    assert _profile(_raw(STEADY, revenue=1e8))[1] == dp.SHOWN


def test_a_roic_below_the_floor_reports_both_numbers():
    low = {**STEADY, 2025: (100.0, 500.0)}                  # 15.8% against a floor of 18%
    prof, status, detail = _profile(_raw(low))
    assert (prof, status) == (None, dp.NOT_TOP_FIFTH)
    assert detail == {"period_end": "2025-12-31", "roic": 100.0 * 0.79 / 500.0, "floor": 0.18}


def test_a_roic_exactly_on_the_floor_is_in():
    # 0.18 = oi x 0.79 / 395 -> oi = 90; ties at the floor are all in, as in quintile_floor.
    on = {**STEADY, 2025: (90.0, 395.0)}
    assert _profile(_raw(on), table={**_table(), "floors": dict.fromkeys(range(2022, 2026), 90.0 * 0.79 / 395.0)})[1] == dp.SHOWN


def test_one_fiscal_year_past_the_table_is_ranked_against_the_table_year():
    later = {y + 1: v for y, v in STEADY.items()}           # latest year end 2026-12-31
    prof, status, _ = _profile(_raw(later), today=date(2027, 3, 15))
    assert status == dp.SHOWN and (prof.bucket, prof.reference_year) == (2026, 2025)


def test_two_fiscal_years_past_the_table_abstains():
    later = {y + 2: v for y, v in STEADY.items()}
    assert _profile(_raw(later), today=date(2028, 3, 15)) == (
        None, dp.REFERENCE_OUT_OF_DATE, {"period_end": "2027-12-31", "table_year": 2025})
    assert _profile(_raw(later), today=date(2028, 3, 15), max_gap=2)[1] == dp.SHOWN


def test_a_fiscal_year_before_the_tables_history_abstains():
    earlier = {y - 1: v for y, v in STEADY.items()}         # latest year end 2024-12-31
    assert _profile(_raw(earlier), today=date(2025, 3, 15))[1] == dp.REFERENCE_OUT_OF_DATE


def test_no_predictor_with_one_year_of_history():
    assert _profile(_raw({2025: (190.0, 500.0)})) == (
        None, dp.NO_PREDICTOR, {"period_end": "2025-12-31"})


def test_two_years_give_capital_growth_and_no_steadiness():
    prof, status, _ = _profile(_raw({2024: (152.0, 400.0), 2025: (216.0, 540.0)}))
    assert status == dp.SHOWN and prof.years_seen == 2
    assert prof.capital_growth is not None and prof.investment_third == 0
    assert prof.stability_spread is None and prof.stability_third is None


def test_a_prior_year_without_a_roic_gives_steadiness_and_no_capital_growth():
    years = {2021: (152.0, 400.0), 2022: (152.0, 400.0), 2023: (152.0, 400.0),
             2024: (152.0, 50.0), 2025: (190.0, 500.0)}     # 2024: capital under the floor
    prof, status, _ = _profile(_raw({y: v for y, v in years.items() if y >= 2022}))
    assert status == dp.SHOWN and prof.years_seen == 3
    assert prof.capital_growth is None and prof.investment_third is None
    assert prof.stability_third is not None


def test_a_fact_filed_after_the_as_of_date_cannot_change_the_profile():
    raw = _raw(STEADY)
    before = _profile(raw, today=date(2026, 9, 1))
    # A restatement of the latest year and of the year before, filed 200 days after the year end.
    for tag, val in (("OperatingIncomeLoss", 999.0), ("StockholdersEquity", 123.0)):
        rows = raw["facts"]["us-gaap"][tag]["units"]["USD"]
        for src in (rows[-1], rows[-2]):
            rows.append({**src, "val": val, "filed": "2026-07-19", "form": "10-K/A"})
    assert _profile(raw, today=date(2026, 9, 1)) == before
    assert before[1] == dp.SHOWN


def test_before_the_as_of_date_the_profile_reads_what_is_on_file_today():
    raw = _raw(STEADY)                                      # filed 2026-02-19
    prof, status, _ = _profile(raw, today=date(2026, 2, 19))
    assert status == dp.SHOWN and prof.period_end == "2025-12-31"
    # The day before, the latest year on file is 2024, which the table's history does not reach.
    assert _profile(raw, today=date(2026, 2, 18))[1] == dp.REFERENCE_OUT_OF_DATE


@pytest.mark.parametrize("end, kept", [("2025-12-15", False), ("2025-12-16", True),
                                       ("2026-01-15", True), ("2026-01-16", False)])
def test_capital_growth_is_read_only_across_about_one_year(end, kept):
    # 349, 350, 380 and 381 days after 2024-12-31. A fiscal-year change puts two year ends in
    # one bucket, and the growth from "the year before" would then span more or less than a
    # year while the section calls it "the last year".
    prof, status, _ = _profile(_raw(STEADY, ends={2025: end}), today=date(2026, 6, 1))
    assert status == dp.SHOWN
    assert (prof.capital_growth is not None) is kept
    assert prof.stability_third is not None


def test_a_changed_year_end_does_not_read_two_year_growth():
    # Year ends 2023-12-31, then 2024-06-30 (a six-month stub is not an annual fact) and
    # 2025-06-30: buckets 2023 and 2024, and nothing in between.
    years = {2022: (152.0, 400.0), 2023: (152.0, 400.0), 2024: (190.0, 800.0)}
    raw = _raw(years, ends={2022: "2022-12-31", 2023: "2023-12-31", 2024: "2025-06-30"})
    prof, status, _ = _profile(raw, today=date(2025, 9, 1), table=_table(2024))
    assert status == dp.SHOWN
    assert prof.capital_growth is None                      # 2023 -> 2025 is 547 days


@pytest.mark.parametrize("value, want", [
    (1.0, 0), (2.0, 0), (2.5, 1),       # rank 1/12, 3/12, then exactly 1/3: the middle
    (3.0, 1), (4.0, 1), (4.5, 2),       # 5/12, 7/12, then exactly 2/3: the top
    (5.0, 2), (99.0, 2), (-1.0, 0)])
def test_thirds_use_the_studys_cut_and_are_exact_on_a_boundary(value, want):
    assert dp.third(value, [1.0, 2.0, 3.0, 4.0, 5.0, 6.0]) == want


def test_a_tie_shares_the_mid_rank():
    # Three equal values at positions 2..4 of 9 share rank (2 + 5) / 2 / 9 = 0.389: the middle.
    assert dp.third(0.0, [-3, -2, 0, 0, 0, 1, 2, 3, 4]) == 1


def test_cut_points_are_not_oriented():
    cuts = dp.cut_points(_table())
    assert cuts["investment"] == (-0.05, 0.20)      # slowest third: growth of -5% or less
    assert cuts["stability"] == (0.05, 0.15)


# ---------------------------------------------------------------- orientation, on the REAL table

REAL = dp.load_table()


def _real(years, **kw):
    return _profile(_raw(years, **kw), table=REAL)[0]


def test_the_committed_table_loads():
    assert REAL is not None and REAL["table_year"] == 2025


def test_on_the_real_table_fast_capital_growth_is_the_fastest_growing_third():
    high = (400.0, 400.0)                                   # ROIC 79%
    fast = _real({2022: high, 2023: high, 2024: high, 2025: (600.0, 600.0)})        # +50%
    slow = _real({2022: high, 2023: high, 2024: high, 2025: (360.0, 360.0)})        # -10%
    assert (fast.investment_third, slow.investment_third) == (0, 2)
    assert fast.capital_growth > 0 > slow.capital_growth


def test_on_the_real_table_a_steady_rank_is_the_steadiest_third():
    high = (400.0, 400.0)
    steady = _real(dict.fromkeys(range(2022, 2026), high))
    swings = _real({2022: high, 2023: (10.0, 400.0), 2024: (400.0, 400.0), 2025: high})
    assert (steady.stability_third, swings.stability_third) == (2, 0)
    assert steady.stability_spread < swings.stability_spread
