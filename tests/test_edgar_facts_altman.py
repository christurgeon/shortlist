"""Altman Z balance inputs on the harness path: raw us-gaap concepts at the newest
balance-sheet instant, all-or-nothing."""
from __future__ import annotations

import pandas as pd
import pytest

from shortlist.providers._edgar_facts import extract_financials

FY = ["2025-12-31 (FY)", "2024-12-31 (FY)"]
INSTANT = ["2025-12-31", "2024-12-31"]


def _income() -> pd.DataFrame:
    return pd.DataFrame([
        {"label": "Revenue", "concept": "us-gaap_Revenues",
         "standard_concept": "Revenue", "dimension": False, "level": 1,
         FY[0]: 500.0, FY[1]: 450.0},
    ])


def _row(concept, vals, standard_concept=None, dimension=False, level=2):
    d = {"label": concept, "concept": f"us-gaap_{concept}",
         "standard_concept": standard_concept, "dimension": dimension, "level": level}
    d.update(dict(zip(INSTANT, vals, strict=True)))
    return d


def _balance(**overrides):
    rows = {
        "Assets": _row("Assets", [1000.0, 900.0], standard_concept="Assets", level=1),
        "AssetsCurrent": _row("AssetsCurrent", [400.0, 380.0]),
        "LiabilitiesCurrent": _row("LiabilitiesCurrent", [250.0, 240.0]),
        "Liabilities": _row("Liabilities", [600.0, 560.0]),
        "RetainedEarningsAccumulatedDeficit":
            _row("RetainedEarningsAccumulatedDeficit", [-120.0, -80.0]),
        "StockholdersEquity": _row("StockholdersEquity", [400.0, 340.0]),
    }
    rows.update(overrides)
    return pd.DataFrame([r for r in rows.values() if r is not None])


def _extract(bal):
    return extract_financials(_income(), pd.DataFrame(), bal, shares_diluted=None)


def test_extracts_newest_instant_balance_inputs():
    fin = _extract(_balance())
    assert fin.balance_sheet_end == "2025-12-31"
    assert fin.current_assets == 400.0
    assert fin.current_liabilities == 250.0
    assert fin.total_liabilities == 600.0
    assert fin.retained_earnings == -120.0     # an accumulated deficit stays negative


def test_matched_on_raw_concept_not_standard_concept():
    wrong = _row("AssetsCurrent", [400.0, 380.0], standard_concept="Inventory")
    decoy = _row("InventoryNet", [77.0, 66.0], standard_concept="CurrentAssetsTotal")
    fin = _extract(_balance(AssetsCurrent=wrong, Decoy=decoy))
    assert fin.current_assets == 400.0


def test_dimensional_rows_are_ignored():
    seg = _row("LiabilitiesCurrent", [9.0, 8.0], dimension=True)
    fin = _extract(_balance(Seg=seg))
    assert fin.current_liabilities == 250.0


def test_total_liabilities_falls_back_to_assets_minus_equity():
    fin = _extract(_balance(Liabilities=None))
    assert fin.total_liabilities == pytest.approx(1000.0 - 400.0)


def test_liabilities_fallback_prefers_nci_inclusive_equity():
    nci = _row("StockholdersEquityIncludingPortionAttributableToNoncontrollingInterest",
               [450.0, 380.0])
    fin = _extract(_balance(Liabilities=None, Nci=nci))
    assert fin.total_liabilities == pytest.approx(1000.0 - 450.0)


def test_negative_equity_fallback_gives_liabilities_above_assets():
    neg = _row("StockholdersEquity", [-50.0, -40.0])
    fin = _extract(_balance(Liabilities=None, StockholdersEquity=neg))
    assert fin.total_liabilities == pytest.approx(1050.0)


@pytest.mark.parametrize("missing", [
    "AssetsCurrent", "LiabilitiesCurrent", "RetainedEarningsAccumulatedDeficit", "Assets"])
def test_abstains_wholesale_when_any_input_is_missing(missing):
    fin = _extract(_balance(**{missing: None}))
    assert fin.balance_sheet_end is None
    assert (fin.current_assets, fin.current_liabilities, fin.total_liabilities,
            fin.retained_earnings) == (None, None, None, None)


def test_abstains_when_liabilities_and_equity_both_missing():
    fin = _extract(_balance(Liabilities=None, StockholdersEquity=None))
    assert fin.balance_sheet_end is None


def test_abstains_when_newest_instant_is_empty_for_an_input():
    sparse = _row("AssetsCurrent", [None, 380.0])
    fin = _extract(_balance(AssetsCurrent=sparse))
    assert fin.balance_sheet_end is None


def test_disagreeing_liabilities_rows_abstain_not_fall_back():
    dup = _row("Liabilities", [610.0, 560.0])
    fin = _extract(_balance(Dup=dup))
    assert fin.balance_sheet_end is None


def test_abstains_when_duplicate_rows_disagree():
    dup = _row("RetainedEarningsAccumulatedDeficit", [55.0, 50.0])
    fin = _extract(_balance(Dup=dup))
    assert fin.balance_sheet_end is None


def test_agreeing_duplicate_rows_are_one_value():
    dup = _row("RetainedEarningsAccumulatedDeficit", [-120.0, -80.0])
    fin = _extract(_balance(Dup=dup))
    assert fin.retained_earnings == -120.0


def test_empty_balance_sheet_abstains():
    fin = _extract(pd.DataFrame())
    assert fin.balance_sheet_end is None
