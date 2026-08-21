"""Inventory BALANCE extraction on the harness path
(docs/PLAN_INVENTORY_DECOMPOSITION.md §1.4).

Balance-sheet INSTANT columns (no " (FY)" suffix), matched on the raw `concept`
column. Only the balance is extracted — the cash-flow companion
`IncreaseDecreaseInInventories` is deliberately absent; see the §0.1 sign hazard.
"""
from __future__ import annotations

import pandas as pd

from shortlist.providers._edgar_facts import extract_financials
from shortlist.providers._gaap_tags import INVENTORY_BALANCE_TAG

INV = f"us-gaap_{INVENTORY_BALANCE_TAG}"
FY = ["2025-12-31 (FY)", "2024-12-31 (FY)"]
INSTANT = ["2025-12-31", "2024-12-31"]


def _income() -> pd.DataFrame:
    return pd.DataFrame([
        {"label": "Revenue", "concept": "us-gaap_Revenues",
         "standard_concept": "Revenue", "dimension": False, "level": 1,
         FY[0]: 246.6e6, FY[1]: 237.1e6},
    ])


def _bal(rows: list[dict]) -> pd.DataFrame:
    return pd.DataFrame(rows)


def _inv_row(vals, concept=INV, dimension=False, level=1):
    d = {"label": "Inventories", "concept": concept, "standard_concept": None,
         "dimension": dimension, "level": level}
    d.update(dict(zip(INSTANT, vals, strict=True)))
    return d


def _extract(balance_rows):
    return extract_financials(_income(), pd.DataFrame(), _bal(balance_rows),
                              shares_diluted=None)


def test_extracts_the_inventory_balance_newest_first():
    """HDSN's real balances: 96.247M (FY24) -> 135.923M (FY25)."""
    fin = _extract([_inv_row([135.923e6, 96.247e6])])
    assert fin.inventory == [135.923e6, 96.247e6]


def test_absent_when_the_filer_reports_no_inventory():
    """A bank or services filer has no inventory line. Normal, not a failure."""
    assert _extract([]) .inventory == []


def test_matched_on_raw_concept_not_standard_concept():
    """HDSN's inventory rows carry standard_concept NaN, and edgartools buckets
    InventoryWriteDown as "RestructuringExpenseBenefit" — standard_concept cannot be
    trusted for this tag."""
    row = _inv_row([135.923e6, 96.247e6])
    row["standard_concept"] = "RestructuringExpenseBenefit"   # deliberately wrong
    assert _extract([row]).inventory == [135.923e6, 96.247e6]


def test_dimensional_rows_are_ignored():
    """A segment/member breakdown must never be mistaken for the consolidated total."""
    fin = _extract([
        _inv_row([135.923e6, 96.247e6]),
        _inv_row([50.0e6, 40.0e6], dimension=True),
    ])
    assert fin.inventory == [135.923e6, 96.247e6]


def test_abstains_on_duplicate_non_dimensional_rows():
    """A signed balance must not be summed or guessed at when the statement carries
    two candidate rows at the same level — abstain instead."""
    fin = _extract([
        _inv_row([135.923e6, 96.247e6]),
        _inv_row([10.0e6, 9.0e6]),
    ])
    assert fin.inventory == []


def test_partial_series_is_dropped_not_half_filled():
    """`_series` is all-or-nothing. That property is what makes index-0 pairing with
    the income-statement spine safe, so a gap must yield [] rather than a short list
    that would silently shift the year alignment."""
    row = _inv_row([135.923e6, 96.247e6])
    row[INSTANT[1]] = None
    assert _extract([row]).inventory == []


def test_wrong_concept_is_not_picked_up():
    fin = _extract([_inv_row([1.0e6, 2.0e6], concept="us-gaap_InventoryGross")])
    assert fin.inventory == []


def test_no_cash_flow_inventory_field_exists():
    """Guard against the cut design returning. The cash-flow companion is signed
    OPPOSITELY by the two source paths (raw XBRL + == build; edgartools applies
    preferred_sign -1), and nothing downstream needs it. §0.1."""
    fin = _extract([_inv_row([135.923e6, 96.247e6])])
    assert not [n for n in dir(fin) if "inventory" in n.lower() and n != "inventory"]
