# tests/test_xbrl_facts.py
from datetime import date

import pytest

from shortlist.providers._xbrl_facts import annual_series

def _row(start, end, val, filed, form="10-K"):
    return {"start": start, "end": end, "val": val, "filed": filed, "form": form}

def _inst(end, val, filed, form="10-K"):
    return {"end": end, "val": val, "filed": filed, "form": form}

def _us(concept, rows, unit="USD"):
    return {"facts": {"us-gaap": {concept: {"units": {unit: rows}}}}}

def test_annual_series_filters_future_filings_and_picks_latest_restatement():
    cf = _us("Revenues", [
        _row("2021-01-01", "2021-12-31", 100, "2022-02-01"),
        _row("2021-01-01", "2021-12-31", 105, "2023-02-01"),
        _row("2022-01-01", "2022-12-31", 130, "2024-02-01"),
    ])
    assert annual_series(cf, ["Revenues"], as_of=date(2023, 6, 1)) == {"2021-12-31": 105.0}

def test_annual_series_drops_non_annual_periods():
    cf = _us("Revenues", [
        _row("2022-10-01", "2022-12-31", 30, "2023-02-01"),
        _row("2022-01-01", "2022-12-31", 120, "2023-02-01"),
    ])
    assert annual_series(cf, ["Revenues"], as_of=date(2024, 1, 1)) == {"2022-12-31": 120.0}

def test_annual_series_priority_wins_on_shared_end_not_latest_filed():
    cf = {"facts": {"us-gaap": {
        "Revenues": {"units": {"USD": [_row("2024-02-01", "2025-01-31", 681, "2025-03-01")]}},
        "SalesRevenueNet": {"units": {"USD": [_row("2024-02-01", "2025-01-31", 674, "2025-03-20")]}},
    }}}
    s = annual_series(cf, ["Revenues", "SalesRevenueNet"], as_of=date(2026, 1, 1))
    assert s == {"2025-01-31": 681.0}

def test_annual_series_falls_through_aliases_by_year():
    cf = {"facts": {"us-gaap": {
        "RevenueFromContractWithCustomerExcludingAssessedTax": {"units": {"USD": [
            _row("2018-01-01", "2018-12-31", 110, "2019-02-01")]}},
        "SalesRevenueNet": {"units": {"USD": [
            _row("2017-01-01", "2017-12-31", 90, "2018-02-01")]}},
    }}}
    s = annual_series(
        cf, ["RevenueFromContractWithCustomerExcludingAssessedTax", "SalesRevenueNet"],
        as_of=date(2020, 1, 1))
    assert s == {"2018-12-31": 110.0, "2017-12-31": 90.0}

def test_annual_series_instant_concept_has_no_start():
    cf = _us("StockholdersEquity", [_inst("2022-12-31", 500, "2023-02-01")])
    s = annual_series(cf, ["StockholdersEquity"], as_of=date(2024, 1, 1), instant=True)
    assert s == {"2022-12-31": 500.0}

def test_annual_series_skips_rows_with_unparseable_dates():
    cf = _us("Revenues", [
        _row("2021-01-01", "2021-12-31", 100, ""),           # empty filed -> skipped
        _row("not-a-date", "2020-12-31", 80, "2021-02-01"),  # bad start -> skipped
        _row("2019-01-01", "2019-12-31", 90, "2020-02-01"),  # good
    ])
    assert annual_series(cf, ["Revenues"], as_of=date(2024, 1, 1)) == {"2019-12-31": 90.0}

def test_annual_series_filed_equal_as_of_is_inclusive():
    cf = _us("Revenues", [_row("2021-01-01", "2021-12-31", 100, "2022-02-01")])
    assert annual_series(cf, ["Revenues"], as_of=date(2022, 2, 1)) == {"2021-12-31": 100.0}

def test_annual_series_excludes_future_period_end_even_if_filed_in_past():
    # Pathological/malformed row: period end is AFTER as_of but filed before it.
    # Must be excluded — a backtest at as_of can't know a period that hasn't ended.
    cf = _us("Revenues", [
        _row("2023-01-01", "2023-12-31", 200, "2023-02-01"),   # future end, past filed
        _row("2021-01-01", "2021-12-31", 100, "2022-02-01"),   # legit
    ])
    assert annual_series(cf, ["Revenues"], as_of=date(2023, 6, 1)) == {"2021-12-31": 100.0}

def test_annual_series_same_date_tie_keeps_the_first_row():
    cf = _us("Revenues", [_row("2021-01-01", "2021-12-31", 100, "2022-02-01"),
                          _row("2021-01-01", "2021-12-31", 101, "2022-02-01")])
    assert annual_series(cf, ["Revenues"], as_of=date(2024, 1, 1)) == {"2021-12-31": 100.0}

def test_annual_series_reads_dei_only_when_us_gaap_lacks_the_concept():
    dei = {"EntityCommonStockSharesOutstanding": {"units": {"shares": [
        _inst("2021-12-31", 7, "2022-02-01")]}}}
    kw = dict(as_of=date(2024, 1, 1), instant=True, units=("shares",))
    cf = {"facts": {"us-gaap": {}, "dei": dei}}
    assert annual_series(cf, ["EntityCommonStockSharesOutstanding"], **kw) == {"2021-12-31": 7.0}
    gaap = {"EntityCommonStockSharesOutstanding": {"units": {"shares": [
        _inst("2021-12-31", 9, "2022-02-01")]}}}
    cf = {"facts": {"us-gaap": gaap, "dei": dei}}
    assert annual_series(cf, ["EntityCommonStockSharesOutstanding"], **kw) == {"2021-12-31": 9.0}
    cf = {"facts": {"us-gaap": {"EntityCommonStockSharesOutstanding": {}}, "dei": dei}}
    assert annual_series(cf, ["EntityCommonStockSharesOutstanding"], **kw) == {}

def test_annual_series_reads_units_in_the_order_given():
    node = {"units": {"USD": [_inst("2021-12-31", 1, "2022-02-01")],
                      "EUR": [_inst("2021-12-31", 2, "2022-02-01")]}}
    cf = {"facts": {"us-gaap": {"X": node}}}
    kw = dict(as_of=date(2024, 1, 1), instant=True)
    assert annual_series(cf, ["X"], units=("USD", "EUR"), **kw) == {"2021-12-31": 1.0}
    assert annual_series(cf, ["X"], units=("EUR", "USD"), **kw) == {"2021-12-31": 2.0}

def test_annual_series_skips_a_malformed_start_either_side_of_as_of():
    cf = _us("Revenues", [_row("not-a-date", "2021-12-31", 100, "2022-02-01"),
                          _row("2020-01-01", "2020-12-31", 90, "2021-02-01")])
    assert annual_series(cf, ["Revenues"], as_of=date(2021, 6, 1)) == {"2020-12-31": 90.0}
    assert annual_series(cf, ["Revenues"], as_of=date(2024, 1, 1)) == {"2020-12-31": 90.0}

def test_annual_series_an_unparseable_val_raises_only_when_picked():
    cf = _us("Revenues", [_row("2020-01-01", "2020-12-31", 90, "2021-02-01"),
                          _row("2021-01-01", "2021-12-31", "n/a", "2022-02-01")])
    assert annual_series(cf, ["Revenues"], as_of=date(2021, 6, 1)) == {"2020-12-31": 90.0}
    with pytest.raises(ValueError):
        annual_series(cf, ["Revenues"], as_of=date(2022, 6, 1))
    # A newer row seen first is never replaced by the older bad one, so it is never parsed.
    later_first = _us("Revenues", [_row("2021-01-01", "2021-12-31", 101, "2023-02-01"),
                                   _row("2021-01-01", "2021-12-31", "n/a", "2022-02-01")])
    assert annual_series(later_first, ["Revenues"], as_of=date(2024, 1, 1)) == {"2021-12-31": 101.0}
    # Seen first, it is picked before the newer row replaces it.
    bad_first = _us("Revenues", [_row("2021-01-01", "2021-12-31", "n/a", "2022-02-01"),
                                 _row("2021-01-01", "2021-12-31", 101, "2023-02-01")])
    with pytest.raises(ValueError):
        annual_series(bad_first, ["Revenues"], as_of=date(2024, 1, 1))

def test_annual_series_a_non_string_filed_raises_at_every_as_of():
    cf = _us("Revenues", [_row("2021-01-01", "2021-12-31", 100, 20220201),
                          _row("2019-01-01", "2019-12-31", 90, "2020-02-01")])
    for as_of in (date(1990, 1, 1), date(2021, 1, 1), date(9999, 12, 31)):
        with pytest.raises(TypeError):
            annual_series(cf, ["Revenues"], as_of=as_of)

def test_annual_series_a_basic_format_end_is_its_own_key():
    cf = _us("Revenues", [_row("2021-01-01", "2021-12-31", 100, "2022-02-01"),
                          _row("2021-01-01", "20211231", 105, "2023-02-01")])
    assert annual_series(cf, ["Revenues"], as_of=date(2024, 1, 1)) == {
        "2021-12-31": 100.0, "20211231": 105.0}

def test_gross_profit_reads_cogs_only_when_gross_profit_is_empty():
    bad_cogs = _row("2021-01-01", "2021-12-31", 40, 20220201)
    gaap = {"Revenues": {"units": {"USD": [_row("2021-01-01", "2021-12-31", 100, "2022-02-01")]}},
            "CostOfRevenue": {"units": {"USD": [bad_cogs]}},
            "GrossProfit": {"units": {"USD": [_row("2021-01-01", "2021-12-31", 60, "2022-06-01")]}}}
    facts = {"facts": {"us-gaap": gaap}}
    assert extract_panel(facts, date(2023, 1, 1)).gross_profit == {"2021-12-31": 60.0}
    with pytest.raises(TypeError):                     # GrossProfit not yet filed
        extract_panel(facts, date(2022, 3, 1))
    del gaap["GrossProfit"]
    with pytest.raises(TypeError):
        extract_panel(facts, date(2023, 1, 1))


# Origin/main's single-pass `annual_series`, frozen as the oracle for the fuzz below.
from datetime import timedelta
import random

from shortlist.providers._xbrl_facts import (
    _ANNUAL_FORMS,
    _MAX_PERIOD_DAYS,
    _MIN_PERIOD_DAYS,
    COGS,
    REVENUE,
    AnnualFacts,
)


def _oracle_annual_series(facts, concepts, as_of, *, instant=False, units=("USD",)):
    gaap = facts.get("facts", {}).get("us-gaap", {})
    dei = facts.get("facts", {}).get("dei", {})
    out = {}
    for concept in concepts:
        node = gaap[concept] if concept in gaap else dei.get(concept)
        if not node:
            continue
        chosen = {}
        for unit in units:
            for f in node.get("units", {}).get(unit, []):
                form = f.get("form")
                if form not in _ANNUAL_FORMS:
                    continue
                filed, end, val = f.get("filed"), f.get("end"), f.get("val")
                if None in (filed, end, val):
                    continue
                try:
                    filed_d = date.fromisoformat(filed)
                    end_d = date.fromisoformat(end)
                    if not instant:
                        start = f.get("start")
                        if start is None:
                            continue
                        span = (end_d - date.fromisoformat(start)).days
                        if not (_MIN_PERIOD_DAYS <= span <= _MAX_PERIOD_DAYS):
                            continue
                except ValueError:
                    continue
                if filed_d > as_of or end_d > as_of:
                    continue
                prev = chosen.get(end)
                if prev is None or filed_d > prev[0]:
                    chosen[end] = (filed_d, float(val))
        for end, (_filed, val) in chosen.items():
            out.setdefault(end, val)
    return out


_FUZZ_ENDS = ("2019-12-31", "2020-12-31", "2021-06-30", "2021-12-31", "20211231", "2022-12-31")
_FUZZ_AS_OF = (date(2019, 6, 1), date(2021, 1, 1), date(2022, 3, 1), date(2023, 1, 1),
               date(9999, 12, 31))
_FUZZ_UNITS = (("USD",), ("USD", "shares"), ("shares", "USD"))
# Each of these makes the row raise when it is parsed or picked.
_FUZZ_BAD = (("filed", 20220201), ("end", 20211231), ("start", 20210101), ("val", "n/a"),
             ("val", [1]))


def _fuzz_row(rng):
    end = rng.choice(_FUZZ_ENDS)
    e = date.fromisoformat(end)
    row = {"end": end, "form": rng.choice(("10-K", "10-K", "10-K/A", "20-F", "10-Q", None)),
           "val": rng.choice((1.0, 2, -3.5, "7", None)),
           "filed": rng.choice(((e + timedelta(days=40)).isoformat(),
                                (e + timedelta(days=400)).isoformat(),
                                (e - timedelta(days=10)).isoformat(), "", "2021-13-01"))}
    start = rng.choice(((e - timedelta(days=364)).isoformat(), (e - timedelta(days=90)).isoformat(),
                        "not-a-date", None))
    if start is not None:
        row["start"] = start
    return row


def _fuzz_facts(rng):
    """(facts, the number of rows made to raise)."""
    tree = {"us-gaap": {}, "dei": {}}
    for ns, concepts in (("us-gaap", "ABC"), ("dei", "AD")):
        for c in concepts:
            if rng.random() < 0.3:
                continue
            if rng.random() < 0.1:
                tree[ns][c] = {}
                continue
            tree[ns][c] = {"units": {u: [_fuzz_row(rng) for _ in range(rng.randint(0, 6))]
                                     for u in ("USD", "shares") if rng.random() < 0.7}}
    rows = [r for ns in tree.values() for node in ns.values()
            for rs in node.get("units", {}).values() for r in rs]
    n_bad = rng.choice((0, 0, 0, 1, 1, 2)) if rows else 0
    for r in rng.sample(rows, min(n_bad, len(rows))):
        key, val = rng.choice(_FUZZ_BAD)
        r[key] = val
    return {"facts": tree}, n_bad


def _outcome(fn, *args, **kw):
    try:
        return "ok", list(fn(*args, **kw).items())
    except Exception as e:
        return "raised", type(e)


def _fuzz_cases():
    rng = random.Random(20261022)
    for _ in range(500):
        facts, n_bad = _fuzz_facts(rng)
        concepts = rng.sample("ABCD", rng.randint(1, 3))
        yield facts, n_bad, concepts


def _same(got, want, n_bad):
    # With two bad rows the type may differ (the one parsed first raises); never whether.
    return got == want if n_bad <= 1 else got[0] == want[0] and (got[0] == "raised" or got == want)


def test_annual_series_matches_the_single_pass_oracle_on_fuzzed_facts():
    raised = compared = 0
    for facts, n_bad, concepts in _fuzz_cases():
        for as_of in _FUZZ_AS_OF:
            for instant in (False, True):
                for units in _FUZZ_UNITS:
                    kw = dict(instant=instant, units=units)
                    want = _outcome(_oracle_annual_series, facts, concepts, as_of, **kw)
                    got = _outcome(annual_series, facts, concepts, as_of, **kw)
                    assert _same(got, want, n_bad), (facts, concepts, as_of, kw, got, want)
                    raised += want[0] == "raised"
                    compared += 1
    assert compared == 500 * len(_FUZZ_AS_OF) * 2 * len(_FUZZ_UNITS)
    assert 0.05 * compared < raised < 0.5 * compared     # both outcomes are exercised



def _oracle_gross_profit(facts, as_of):
    gp = _oracle_annual_series(facts, ["GrossProfit"], as_of)
    if gp:
        return gp
    rev = _oracle_annual_series(facts, REVENUE, as_of)
    cogs = _oracle_annual_series(facts, COGS, as_of)
    return {e: rev[e] - cogs[e] for e in rev if e in cogs}


def test_annual_facts_reuses_one_parse_across_as_of_dates_and_matches_the_oracle():
    # One object per case, the grid read in a shuffled order: a memoised parse must give every
    # as-of date what a fresh single-pass read gives, and keep raising where that raises.
    rng = random.Random(7)
    for facts, n_bad, concepts in _fuzz_cases():
        af = AnnualFacts(facts)
        grid = [(a, i, u) for a in _FUZZ_AS_OF for i in (False, True) for u in _FUZZ_UNITS]
        rng.shuffle(grid)
        for as_of, instant, units in grid:
            kw = dict(instant=instant, units=units)
            want = _outcome(_oracle_annual_series, facts, concepts, as_of, **kw)
            got = _outcome(af.series, concepts, as_of, **kw)
            assert _same(got, want, n_bad), (facts, concepts, as_of, kw, got, want)


def test_annual_facts_series_keys_on_instant_and_units():
    gaap = {"Revenues": {"units": {
        "USD": [_row("2021-01-01", "2021-12-31", 100, "2022-02-01"),
                _inst("2020-12-31", 90, "2021-02-01")],
        "shares": [_inst("2020-12-31", 5, "2021-02-01")]}}}
    facts = {"facts": {"us-gaap": gaap}}
    af = AnnualFacts(facts)
    for as_of in (date(2020, 1, 1), date(2021, 6, 1), date(2024, 1, 1)):
        for kw in ({}, {"instant": True}, {"units": ("shares",)},
                   {"instant": True, "units": ("USD", "shares")}):
            assert af.series(["Revenues"], as_of, **kw) == annual_series(
                facts, ["Revenues"], as_of, **kw), (as_of, kw)
    assert af.series(["Revenues"], date(2024, 1, 1)) == {"2021-12-31": 100.0}
    assert af.series(["Revenues"], date(2024, 1, 1), instant=True) == {
        "2021-12-31": 100.0, "2020-12-31": 90.0}


def test_annual_facts_gross_profit_matches_the_rule_and_reads_cogs_only_when_needed():
    bad_cogs = _row("2021-01-01", "2021-12-31", 35, 20220201)
    gaap = {"Revenues": {"units": {"USD": [_row("2021-01-01", "2021-12-31", 100, "2022-02-01")]}},
            "CostOfRevenue": {"units": {"USD": [bad_cogs]}},
            "GrossProfit": {"units": {"USD": [_row("2021-01-01", "2021-12-31", 60, "2022-06-01")]}}}
    af = AnnualFacts({"facts": {"us-gaap": gaap}})
    assert af.gross_profit(date(2023, 1, 1)) == {"2021-12-31": 60.0}
    for _ in range(2):                                 # a failed parse is not memoised
        with pytest.raises(TypeError):
            af.gross_profit(date(2022, 3, 1))
    bad_cogs["filed"] = "2022-02-01"
    del gaap["GrossProfit"]
    af = AnnualFacts({"facts": {"us-gaap": gaap}})
    assert af.gross_profit(date(2023, 1, 1)) == {"2021-12-31": 65.0}
    for facts, n_bad, _ in _fuzz_cases():
        # The fuzzed concepts are named A-D, so map them onto the gross-profit families.
        tree = {ns: {{"A": "GrossProfit", "B": REVENUE[1], "C": COGS[0], "D": COGS[1]}[c]: node
                     for c, node in nodes.items()} for ns, nodes in facts["facts"].items()}
        mapped = {"facts": tree}
        af = AnnualFacts(mapped)
        for as_of in _FUZZ_AS_OF:
            want = _outcome(_oracle_gross_profit, mapped, as_of)
            assert _same(_outcome(af.gross_profit, as_of), want, n_bad), (mapped, as_of)

from shortlist.providers._xbrl_facts import align_fcf, sum_aligned, ratio_latest, desc, latest

def test_align_fcf_subtracts_capex_only_on_shared_ends():
    fcf = align_fcf({"2022-12-31": 200.0, "2021-12-31": 180.0, "2020-12-31": 160.0},
                    {"2022-12-31": 50.0, "2021-12-31": 40.0})   # 2020 capex missing
    assert fcf == {"2022-12-31": 150.0, "2021-12-31": 140.0}    # 2020 dropped

def test_sum_aligned_adds_only_on_shared_ends():
    assert sum_aligned({"2022-12-31": 300.0, "2021-12-31": 280.0},
                       {"2022-12-31": 30.0}) == {"2022-12-31": 330.0}

def test_ratio_latest_uses_latest_common_end_and_guards_zero():
    assert ratio_latest({"2022-12-31": 150.0, "2021-12-31": 130.0},
                        {"2022-12-31": 1200.0, "2020-12-31": 1000.0}) == 150.0 / 1200.0
    assert ratio_latest({"2022-12-31": 1.0}, {"2022-12-31": 0.0}) is None   # div-by-zero
    assert ratio_latest({"2022-12-31": 1.0}, {"2021-12-31": 2.0}) is None   # no common end

def test_desc_returns_values_newest_first():
    assert desc({"2020-12-31": 1.0, "2022-12-31": 3.0, "2021-12-31": 2.0}) == [3.0, 2.0, 1.0]

def test_latest_returns_most_recent_or_none():
    assert latest({"2020-12-31": 1.0, "2022-12-31": 3.0, "2021-12-31": 2.0}) == 3.0
    assert latest({}) is None


# ---------------------------------------------------------------------------
# Task 5: XbrlPanel + extract_panel
# ---------------------------------------------------------------------------
from shortlist.providers._xbrl_facts import extract_panel


def _annual(concept, rows, unit="USD"):
    return {concept: {"units": {unit: rows}}}


def test_extract_panel_builds_end_keyed_dicts():
    gaap = {}
    gaap.update(_annual("Revenues", [
        _row("2021-01-01", "2021-12-31", 1000, "2022-02-01"),
        _row("2022-01-01", "2022-12-31", 1200, "2023-02-01")]))
    gaap.update(_annual("NetIncomeLoss", [
        _row("2021-01-01", "2021-12-31", 100, "2022-02-01"),
        _row("2022-01-01", "2022-12-31", 150, "2023-02-01")]))
    gaap.update(_annual("NetCashProvidedByUsedInOperatingActivities", [
        _row("2022-01-01", "2022-12-31", 200, "2023-02-01")]))
    gaap.update(_annual("PaymentsToAcquirePropertyPlantAndEquipment", [
        _row("2022-01-01", "2022-12-31", 50, "2023-02-01")]))
    gaap.update(_annual("StockholdersEquity", [_inst("2022-12-31", 600, "2023-02-01")]))
    facts = {"facts": {"us-gaap": gaap,
                       "dei": _annual("EntityCommonStockSharesOutstanding",
                                      [_inst("2022-12-31", 10, "2023-02-01")],
                                      unit="shares")}}
    p = extract_panel(facts, as_of=date(2024, 1, 1))
    assert p.revenue == {"2022-12-31": 1200.0, "2021-12-31": 1000.0}
    assert p.net_income == {"2022-12-31": 150.0, "2021-12-31": 100.0}
    assert p.fcf == {"2022-12-31": 150.0}        # 200 - 50, only FY2022 has both
    assert p.total_equity == {"2022-12-31": 600.0}
    assert p.shares == 10.0


def test_extract_panel_collects_diluted_shares_series():
    gaap = _annual("WeightedAverageNumberOfDilutedSharesOutstanding", [
        _row("2021-01-01", "2021-12-31", 1000, "2022-02-01"),
        _row("2022-01-01", "2022-12-31", 950, "2023-02-01")], unit="shares")
    p = extract_panel({"facts": {"us-gaap": gaap}}, as_of=date(2024, 1, 1))
    assert p.diluted_shares == {"2022-12-31": 950.0, "2021-12-31": 1000.0}


def test_extract_panel_gross_profit_falls_back_to_revenue_minus_cogs():
    gaap = {}
    gaap.update(_annual("Revenues", [_row("2022-01-01", "2022-12-31", 1200, "2023-02-01")]))
    gaap.update(_annual("CostOfGoodsAndServicesSold",
                        [_row("2022-01-01", "2022-12-31", 700, "2023-02-01")]))
    p = extract_panel({"facts": {"us-gaap": gaap}}, as_of=date(2024, 1, 1))
    assert p.gross_profit == {"2022-12-31": 500.0}   # 1200 - 700


def test_extract_panel_total_debt_sums_lt_and_current():
    gaap = {}
    gaap.update(_annual("Revenues", [_row("2022-01-01", "2022-12-31", 1000, "2023-02-01")]))
    gaap.update(_annual("LongTermDebtNoncurrent", [_inst("2022-12-31", 300, "2023-02-01")]))
    gaap.update(_annual("LongTermDebtCurrent", [_inst("2022-12-31", 50, "2023-02-01")]))
    p = extract_panel({"facts": {"us-gaap": gaap}}, as_of=date(2024, 1, 1))
    assert p.total_debt == {"2022-12-31": 350.0}

def test_extract_panel_diluted_eps_uses_usd_per_share_unit():
    gaap = _annual("EarningsPerShareDiluted",
                   [_row("2022-01-01", "2022-12-31", 3.5, "2023-02-01")], unit="USD/shares")
    # a stray USD-denominated row must NOT be picked up for EPS
    gaap["EarningsPerShareDiluted"]["units"]["USD"] = [
        _row("2022-01-01", "2022-12-31", 999.0, "2023-02-01")]
    p = extract_panel({"facts": {"us-gaap": gaap}}, as_of=date(2024, 1, 1))
    assert p.diluted_eps == {"2022-12-31": 3.5}

def test_extract_panel_shares_picks_latest_instant():
    gaap = _annual("Revenues", [_row("2022-01-01", "2022-12-31", 1000, "2023-02-01")])
    dei = _annual("EntityCommonStockSharesOutstanding", [
        _inst("2021-12-31", 8, "2022-02-01"),
        _inst("2022-12-31", 10, "2023-02-01")], unit="shares")
    p = extract_panel({"facts": {"us-gaap": gaap, "dei": dei}}, as_of=date(2024, 1, 1))
    assert p.shares == 10.0


# ---------------------------------------------------------------------------
# Task 6: panel_to_metrics — StockMetrics derivation from XbrlPanel
# ---------------------------------------------------------------------------
from shortlist.providers._xbrl_facts import XbrlPanel, panel_to_metrics

def test_panel_to_metrics_derives_legs_and_price_dependent_value():
    p = XbrlPanel(
        revenue={"2022-12-31": 1200, "2021-12-31": 1100, "2020-12-31": 1000},
        net_income={"2022-12-31": 150, "2021-12-31": 130, "2020-12-31": 100},
        fcf={"2022-12-31": 120, "2021-12-31": 110, "2020-12-31": 90},
        diluted_eps={"2022-12-31": 3.0, "2021-12-31": 2.6, "2020-12-31": 2.0},
        gross_profit={"2022-12-31": 600, "2021-12-31": 550, "2020-12-31": 500},
        total_equity={"2022-12-31": 600, "2021-12-31": 560, "2020-12-31": 520},
        total_debt={"2022-12-31": 300, "2021-12-31": 280, "2020-12-31": 260},
        operating_income={"2022-12-31": 200, "2021-12-31": 180, "2020-12-31": 150},
        interest_expense={"2022-12-31": 20, "2021-12-31": 18, "2020-12-31": 15},
        pretax_income={"2022-12-31": 180, "2021-12-31": 162, "2020-12-31": 135},
        income_tax={"2022-12-31": 36, "2021-12-31": 32, "2020-12-31": 27},
        shares=10.0,
    )
    prices = {date(2022, 12, 31): 60.0, date(2021, 12, 31): 52.0, date(2020, 12, 31): 40.0}
    m = panel_to_metrics(p, ticker="TST", sic="3711", price=60.0,
                         price_at=lambda d: prices.get(d))
    assert m.ticker == "TST" and m.sic == "3711"
    assert round(m.net_margin, 4) == round(150 / 1200, 4)
    assert round(m.roe, 4) == round(150 / 600, 4)
    assert round(m.debt_to_equity, 4) == round(300 / 600, 4)
    assert round(m.gross_margin, 4) == round(600 / 1200, 4)
    assert round(m.interest_coverage, 4) == round(200 / 20, 4)
    assert m.roic is not None and m.roic_5y_avg is not None
    assert m.revenue_cagr is not None and m.fcf_cagr is not None
    assert m.eps_cagr is not None and m.revenue_growth_persistence == 1.0
    assert round(m.fcf_yield, 4) == round(120 / 600, 4)    # market_cap = 60 * 10 = 600
    assert round(m.pe_ttm, 4) == round(60 / 3.0, 4)
    assert m.pe_median_5y is not None                       # >= 2 annual PE points

def test_panel_to_metrics_aligns_ratios_by_end_not_position():
    # gross_profit is MISSING the newest year that revenue has -> must pair by the
    # common fiscal end (2021), never gp_values[0]/rev_values[0].
    p = XbrlPanel(revenue={"2022-12-31": 1200, "2021-12-31": 1100},
                  gross_profit={"2021-12-31": 550})
    m = panel_to_metrics(p, ticker="X", sic=None, price=None, price_at=lambda d: None)
    assert round(m.gross_margin, 4) == round(550 / 1100, 4)

def test_panel_to_metrics_sets_fcf_positive_from_latest_fcf_sign():
    # fcf_positive drives the stage-aware negative_fcf gate; it must mirror the
    # harness bridge (sign of the latest-FY FCF), None when no FCF year is knowable.
    mk = lambda p: panel_to_metrics(p, ticker="T", sic=None, price=None,
                                    price_at=lambda d: None)
    ocf = {"2022-12-31": 1.0, "2021-12-31": 1.0}                      # latest end = 2022
    pos = XbrlPanel(fcf={"2022-12-31": 120.0, "2021-12-31": -50.0}, ocf=ocf)
    neg = XbrlPanel(fcf={"2022-12-31": -30.0, "2021-12-31": 200.0}, ocf=ocf)
    stale = XbrlPanel(fcf={"2021-12-31": 120.0}, ocf=ocf)            # fcf year < ocf year
    assert mk(pos).fcf_positive is True
    assert mk(neg).fcf_positive is False
    assert mk(stale).fcf_positive is None                            # stale FY -> abstain
    assert mk(XbrlPanel()).fcf_positive is None                      # no FCF -> abstain

def test_panel_fcf_positive_through_extract_panel_subtracts_positive_capex():
    # Real sign convention via align_fcf (panel SUBTRACTS a positive capex, vs the
    # harness which ADDS a negative one). Newest FY burns cash (OCF 100 - capex 150
    # = -50) -> fcf_positive False. Exercises extract_panel, not a hand-signed dict.
    gaap = {}
    gaap.update(_annual("NetCashProvidedByUsedInOperatingActivities",
                        [_row("2023-01-01", "2023-12-31", 100, "2024-02-01")]))
    gaap.update(_annual("PaymentsToAcquirePropertyPlantAndEquipment",
                        [_row("2023-01-01", "2023-12-31", 150, "2024-02-01")]))
    p = extract_panel({"facts": {"us-gaap": gaap}}, as_of=date(2024, 6, 1))
    assert p.fcf == {"2023-12-31": -50.0}
    m = panel_to_metrics(p, ticker="T", sic=None, price=None, price_at=lambda d: None)
    assert m.fcf_positive is False

def test_panel_fcf_positive_abstains_when_latest_fy_lacks_capex():
    # Newest FY (2023) tags OCF but NO capex -> align_fcf drops 2023, so latest(p.fcf)
    # is the STALE 2022. The harness bridge abstains (None) here; the panel MUST too,
    # not report 2022's positive sign as if current -- else a 2023 cash-burner would
    # evade the negative_fcf gate once it's wired onto the XBRL path.
    gaap = {}
    gaap.update(_annual("NetCashProvidedByUsedInOperatingActivities", [
        _row("2022-01-01", "2022-12-31", 200, "2023-02-01"),
        _row("2023-01-01", "2023-12-31", 180, "2024-02-01")]))
    gaap.update(_annual("PaymentsToAcquirePropertyPlantAndEquipment",
                        [_row("2022-01-01", "2022-12-31", 50, "2023-02-01")]))  # 2023 missing
    p = extract_panel({"facts": {"us-gaap": gaap}}, as_of=date(2024, 6, 1))
    assert p.fcf == {"2022-12-31": 150.0}        # 2023 dropped (no capex)
    assert max(p.ocf) == "2023-12-31"            # but OCF knows 2023
    m = panel_to_metrics(p, ticker="T", sic=None, price=None, price_at=lambda d: None)
    assert m.fcf_positive is None                 # latest-FY FCF unknowable -> abstain

def test_panel_to_metrics_value_degrades_when_price_absent():
    p = XbrlPanel(
        revenue={"2022-12-31": 1200, "2021-12-31": 1100, "2020-12-31": 1000},
        net_income={"2022-12-31": 150, "2021-12-31": 130, "2020-12-31": 100},
        gross_profit={"2022-12-31": 600, "2021-12-31": 550, "2020-12-31": 500},
        total_equity={"2022-12-31": 600}, fcf={"2022-12-31": 120},
        diluted_eps={"2022-12-31": 3.0}, shares=10.0)
    m = panel_to_metrics(p, ticker="X", sic=None, price=None, price_at=lambda d: None)
    assert m.market_cap is None and m.fcf_yield is None and m.pe_ttm is None
    assert m.net_margin is not None and m.revenue_cagr is not None

def test_panel_to_metrics_none_safe_on_empty_panel():
    m = panel_to_metrics(XbrlPanel(), ticker="X", sic=None, price=None,
                         price_at=lambda d: None)
    assert m.net_margin is None and m.fcf_yield is None and m.pe_ttm is None
    assert m.revenue_cagr is None and m.roic is None

def test_panel_to_metrics_roic_none_for_negative_invested_capital():
    # Buyback-driven negative equity: eq + dc < 0 must yield roic None, not a
    # backwards (negative) ROIC for a profitable firm.
    p = XbrlPanel(
        operating_income={"2022-12-31": 200},
        total_equity={"2022-12-31": -500}, total_debt={"2022-12-31": 300},
        pretax_income={"2022-12-31": 180}, income_tax={"2022-12-31": 36})
    m = panel_to_metrics(p, ticker="X", sic=None, price=None, price_at=lambda d: None)
    assert m.roic is None and m.roic_5y_avg is None

def test_panel_to_metrics_pe_median_none_with_single_pe_point():
    p = XbrlPanel(revenue={"2022-12-31": 1000}, diluted_eps={"2022-12-31": 4.0})
    m = panel_to_metrics(p, ticker="X", sic="3711", price=80.0, price_at=lambda d: 80.0)
    assert m.pe_ttm == 80.0 / 4.0      # pe_ttm computes from latest EPS
    assert m.pe_median_5y is None      # median_pe needs >= 2 points

def test_panel_stores_ocf_series():
    from datetime import date
    from shortlist.providers._xbrl_facts import extract_panel
    facts = {"facts": {"us-gaap": {
        "NetCashProvidedByUsedInOperatingActivities": {"units": {"USD": [
            {"start": "2021-01-01", "end": "2021-12-31", "val": 200, "filed": "2022-02-01", "form": "10-K"},
            {"start": "2022-01-01", "end": "2022-12-31", "val": 240, "filed": "2023-02-01", "form": "10-K"},
        ]}},
        "PaymentsToAcquirePropertyPlantAndEquipment": {"units": {"USD": [
            {"start": "2022-01-01", "end": "2022-12-31", "val": 40, "filed": "2023-02-01", "form": "10-K"},
        ]}},
    }}}
    panel = extract_panel(facts, date(2023, 6, 1))
    assert panel.ocf == {"2021-12-31": 200, "2022-12-31": 240}

def test_piotroski_aligns_delta_legs_by_fiscal_end_not_position():
    # total_debt is sum_aligned(LT, current) which INTERSECTS ends; if a filer omits
    # the current-debt tag for one year, total_debt drops that year and its desc() list
    # becomes shorter than revenue's. Positional alignment would compare debt_2023/rev_2023
    # vs debt_2021/rev_2022 (mixed years). The panel must align by fiscal end so F5
    # ABSTAINS on the missing year rather than mixing years.
    from shortlist.providers._xbrl_facts import extract_panel, panel_to_metrics
    g = {
        "Revenues": {"units": {"USD": [
            _row("2021-01-01", "2021-12-31", 1000, "2022-02-01"),
            _row("2022-01-01", "2022-12-31", 1100, "2023-02-01"),
            _row("2023-01-01", "2023-12-31", 1300, "2024-02-01")]}},
        "NetIncomeLoss": {"units": {"USD": [
            _row("2021-01-01", "2021-12-31", 100, "2022-02-01"),
            _row("2022-01-01", "2022-12-31", 120, "2023-02-01"),
            _row("2023-01-01", "2023-12-31", 160, "2024-02-01")]}},
        "GrossProfit": {"units": {"USD": [
            _row("2021-01-01", "2021-12-31", 500, "2022-02-01"),
            _row("2022-01-01", "2022-12-31", 560, "2023-02-01"),
            _row("2023-01-01", "2023-12-31", 700, "2024-02-01")]}},
        "NetCashProvidedByUsedInOperatingActivities": {"units": {"USD": [
            _row("2021-01-01", "2021-12-31", 150, "2022-02-01"),
            _row("2022-01-01", "2022-12-31", 200, "2023-02-01"),
            _row("2023-01-01", "2023-12-31", 240, "2024-02-01")]}},
        "LongTermDebtNoncurrent": {"units": {"USD": [
            _inst("2021-12-31", 300, "2022-02-01"),
            _inst("2022-12-31", 290, "2023-02-01"),
            _inst("2023-12-31", 250, "2024-02-01")]}},
        "LongTermDebtCurrent": {"units": {"USD": [   # 2022 current portion missing
            _inst("2021-12-31", 50, "2022-02-01"),
            _inst("2023-12-31", 50, "2024-02-01")]}},
    }
    panel = extract_panel({"facts": {"us-gaap": g}}, date(2024, 6, 1))
    m = panel_to_metrics(panel, ticker="T", sic=None, price=None, price_at=lambda d: None)
    # total_debt has ends {2023, 2021}; F5's latest YoY (2023 vs 2022) needs 2022 debt,
    # which is missing -> F5 abstains. The other 5 legs all pass.
    assert (m.piotroski_f, m.piotroski_f_legs) == (5, 5)

def test_panel_to_metrics_ebit_ev_yield():
    # operating_income=300, net_debt = total_debt 400 - cash 100 = 300,
    # market_cap = price 120 * shares 10 = 1200, EV = 1200 + 300 = 1500,
    # EBIT/EV = 300 / 1500 = 0.20.
    p = XbrlPanel(
        revenue={"2023-12-31": 1000.0},
        operating_income={"2023-12-31": 300.0},
        total_debt={"2023-12-31": 400.0},
        cash={"2023-12-31": 100.0},
        dep_amort={"2023-12-31": 0.0},
        shares=10.0,
    )
    m = panel_to_metrics(p, ticker="TEST", sic=None, price=120.0,
                         price_at=lambda d: None)
    assert m.ebit_ev_yield == pytest.approx(0.20, rel=1e-6)


def test_panel_to_metrics_populates_piotroski_two_years():
    from datetime import date
    from shortlist.providers._xbrl_facts import extract_panel, panel_to_metrics
    # NOTE: extract_panel builds total_debt = sum_aligned(LongTermDebt*, CurrentDebt*)
    # and INTERSECTS fiscal ends — so BOTH a long-term AND a current-debt concept must
    # be present at each end or total_debt comes out empty and F5 (debt/revenue) drops.
    g = {
        "Revenues": {"units": {"USD": [
            {"start": "2021-01-01", "end": "2021-12-31", "val": 1100, "filed": "2022-02-01", "form": "10-K"},
            {"start": "2022-01-01", "end": "2022-12-31", "val": 1300, "filed": "2023-02-01", "form": "10-K"}]}},
        "NetIncomeLoss": {"units": {"USD": [
            {"start": "2021-01-01", "end": "2021-12-31", "val": 120, "filed": "2022-02-01", "form": "10-K"},
            {"start": "2022-01-01", "end": "2022-12-31", "val": 160, "filed": "2023-02-01", "form": "10-K"}]}},
        "GrossProfit": {"units": {"USD": [
            {"start": "2021-01-01", "end": "2021-12-31", "val": 560, "filed": "2022-02-01", "form": "10-K"},
            {"start": "2022-01-01", "end": "2022-12-31", "val": 700, "filed": "2023-02-01", "form": "10-K"}]}},
        "NetCashProvidedByUsedInOperatingActivities": {"units": {"USD": [
            {"start": "2021-01-01", "end": "2021-12-31", "val": 200, "filed": "2022-02-01", "form": "10-K"},
            {"start": "2022-01-01", "end": "2022-12-31", "val": 240, "filed": "2023-02-01", "form": "10-K"}]}},
        "LongTermDebtNoncurrent": {"units": {"USD": [
            {"end": "2021-12-31", "val": 300, "filed": "2022-02-01", "form": "10-K"},
            {"end": "2022-12-31", "val": 250, "filed": "2023-02-01", "form": "10-K"}]}},
        "LongTermDebtCurrent": {"units": {"USD": [
            {"end": "2021-12-31", "val": 50, "filed": "2022-02-01", "form": "10-K"},
            {"end": "2022-12-31", "val": 50, "filed": "2023-02-01", "form": "10-K"}]}},
    }
    # total_debt: 2021 -> 350, 2022 -> 300. F5 di: 300/1300=.231 < 350/1100=.318 -> falling, pass.
    panel = extract_panel({"facts": {"us-gaap": g}}, date(2023, 6, 1))
    m = panel_to_metrics(panel, ticker="T", sic=None, price=None, price_at=lambda d: None)
    # F1 NI>0, F2 OCF>0, F3 OCF>NI, F4 net-margin rising, F5 debt/rev falling, F6 GM rising -> 6/6
    assert (m.piotroski_f, m.piotroski_f_legs) == (6, 6)


# ---------------------------------------------------------------------------
# Asset-growth + accruals (PREDICTIVE_SIGNALS §3)
# ---------------------------------------------------------------------------

def test_extract_panel_collects_assets_instant_series():
    gaap = _annual("Assets", [
        _inst("2021-12-31", 1000, "2022-02-01"),
        _inst("2022-12-31", 1100, "2023-02-01")])
    p = extract_panel({"facts": {"us-gaap": gaap}}, as_of=date(2024, 1, 1))
    assert p.assets == {"2022-12-31": 1100.0, "2021-12-31": 1000.0}


def test_panel_to_metrics_derives_asset_growth_and_accruals():
    p = XbrlPanel(
        revenue={"2022-12-31": 1200, "2021-12-31": 1100},
        net_income={"2022-12-31": 200, "2021-12-31": 150},
        ocf={"2022-12-31": 150, "2021-12-31": 140},
        assets={"2022-12-31": 1100, "2021-12-31": 1000})
    m = panel_to_metrics(p, ticker="X", sic=None, price=None, price_at=lambda d: None)
    # asset_growth = 1100/1000 - 1 = 0.10
    assert m.asset_growth == pytest.approx(0.10)
    # accruals = (NI - CFO) / avg_assets = (200 - 150) / 1050 ; CFO as-reported (no sign flip)
    assert m.accruals == pytest.approx(50.0 / 1050.0)


def test_panel_to_metrics_asset_signals_none_without_assets():
    p = XbrlPanel(
        revenue={"2022-12-31": 1200, "2021-12-31": 1100},
        net_income={"2022-12-31": 200, "2021-12-31": 150},
        ocf={"2022-12-31": 150, "2021-12-31": 140})
    m = panel_to_metrics(p, ticker="X", sic=None, price=None, price_at=lambda d: None)
    assert m.asset_growth is None and m.accruals is None


# ---------------------------------------------------------------------------
# Total shareholder yield (PREDICTIVE_SIGNALS §5)
# ---------------------------------------------------------------------------

def test_extract_panel_sum_family_adds_common_and_preferred():
    # sum_family ADDS distinct members reporting the same end (vs annual_series's
    # priority-with-fallback). Raw companyfacts report positive outflow magnitudes.
    gaap = {}
    gaap.update(_annual("PaymentsOfDividendsCommonStock",
                        [_row("2022-01-01", "2022-12-31", 100, "2023-02-01")]))
    gaap.update(_annual("PaymentsOfDividendsPreferredStockAndPreferenceStock",
                        [_row("2022-01-01", "2022-12-31", 10, "2023-02-01")]))
    gaap.update(_annual("ProceedsFromIssuanceOfLongTermDebt",
                        [_row("2022-01-01", "2022-12-31", 50, "2023-02-01")]))
    gaap.update(_annual("RepaymentsOfDebt",
                        [_row("2022-01-01", "2022-12-31", 200, "2023-02-01")]))
    p = extract_panel({"facts": {"us-gaap": gaap}}, as_of=date(2024, 1, 1))
    assert p.dividends_paid == {"2022-12-31": 110.0}     # 100 common + 10 preferred
    assert p.debt_issuance == {"2022-12-31": 50.0}
    assert p.debt_repayments == {"2022-12-31": 200.0}


def test_panel_to_metrics_derives_shareholder_yield():
    # shareholder_yield = (dividends + repurchases + (repayments - issuance)) / market_cap.
    # Raw companyfacts -> positive magnitudes; ADD dividends + repurchases; net the debt leg.
    p = XbrlPanel(
        revenue={"2022-12-31": 1200},
        shares=100.0,
        dividends_paid={"2022-12-31": 50.0},
        repurchases={"2022-12-31": 150.0},
        debt_repayments={"2022-12-31": 80.0},
        debt_issuance={"2022-12-31": 30.0})
    m = panel_to_metrics(p, ticker="X", sic=None, price=10.0, price_at=lambda d: None)
    # market_cap = 10 * 100 = 1000 ; payout = 50 + 150 + (80 - 30) = 250 -> 0.25
    assert m.market_cap == pytest.approx(1000.0)
    assert m.shareholder_yield == pytest.approx(0.25)


def test_panel_to_metrics_shareholder_yield_net_debt_issuer_negative():
    # A net debt ISSUER (issuance > repayments) -> the net-debt leg goes NEGATIVE and is
    # NOT clamped; with no dividends/buybacks the whole yield can be negative.
    p = XbrlPanel(
        revenue={"2022-12-31": 1200}, shares=100.0,
        debt_repayments={"2022-12-31": 20.0},
        debt_issuance={"2022-12-31": 120.0})
    m = panel_to_metrics(p, ticker="X", sic=None, price=10.0, price_at=lambda d: None)
    # payout = 0 + 0 + (20 - 120) = -100 ; / 1000 = -0.10 (negative, not clamped)
    assert m.shareholder_yield == pytest.approx(-0.10)


def test_panel_to_metrics_shareholder_yield_none_without_financing_or_mktcap():
    # No financing legs -> abstain.
    p = XbrlPanel(revenue={"2022-12-31": 1200}, shares=100.0)
    assert panel_to_metrics(p, ticker="X", sic=None, price=10.0,
                            price_at=lambda d: None).shareholder_yield is None
    # Financing present but no market_cap (no price) -> abstain.
    p2 = XbrlPanel(revenue={"2022-12-31": 1200},
                   dividends_paid={"2022-12-31": 50.0})
    assert panel_to_metrics(p2, ticker="X", sic=None, price=None,
                            price_at=lambda d: None).shareholder_yield is None
