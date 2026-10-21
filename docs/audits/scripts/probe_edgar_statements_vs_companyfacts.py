"""Compare harness EDGAR statement series against SEC companyfacts (10-K, annual duration).

Usage (snapshots from `shortlist-harness --sources edgar --out DIR`):

    uv run python docs/audits/scripts/probe_edgar_statements_vs_companyfacts.py DIR

Prints per-field status counts for the newest fiscal year (OCF, revenue, net income, and
the FCF sign against companyfacts OCF - capex), every non-ok row, and then every year of
OCF / revenue / net income that is more than 2% off. companyfacts is read through the
backtest's month-keyed cache (`.cache/sec_xbrl`), fetched serially on a miss.

The references read the same raw tags the extractor prefers (OCF continuing operations
first, revenue `Revenues` first), so "ok" confirms row selection and scale, not the choice
of concept: a wrong `Revenues` row, or a bucket fallback, is not detected.
"""
from __future__ import annotations

import asyncio
import glob
import gzip
import json
import os
import sys
from collections import Counter
from datetime import date

from shortlist.backtest.cli import _load_companyfacts
from shortlist.backtest.xbrl import cik_for, read_companyfacts_cache
from shortlist.env import load_env

CACHE = ".cache/sec_xbrl"
TOL = 0.02
CONCEPTS = {
    "operating_cash_flow": ["NetCashProvidedByUsedInOperatingActivitiesContinuingOperations",
                            "NetCashProvidedByUsedInOperatingActivities"],
    "revenue": ["Revenues", "RevenueFromContractWithCustomerExcludingAssessedTax",
                "RevenueFromContractWithCustomerIncludingAssessedTax", "SalesRevenueNet",
                "RevenuesNetOfInterestExpense"],
    "net_income": ["NetIncomeLoss", "ProfitLoss"],
    "capex": ["PaymentsToAcquirePropertyPlantAndEquipment", "PaymentsToAcquireProductiveAssets"],
}


def fy_value(facts: dict, names: list[str], end: str):
    g = facts.get("facts", {}).get("us-gaap", {})
    for n in names:
        for r in g.get(n, {}).get("units", {}).get("USD", []):
            if r.get("end") != end or "start" not in r:
                continue
            days = (date.fromisoformat(r["end"]) - date.fromisoformat(r["start"])).days
            if 350 <= days <= 380 and r.get("form", "").startswith("10-K"):
                return r["val"]
    return None


def off(got, want) -> bool:
    return got != want if want == 0 else abs(got - want) / abs(want) >= TOL


def load_snapshots(d: str) -> dict[str, dict]:
    out = {}
    for p in sorted(glob.glob(os.path.join(d, "**", "*.json*"), recursive=True)):
        with (gzip.open(p) if p.endswith(".gz") else open(p)) as fh:
            s = json.load(fh)
        s = s[0] if isinstance(s, list) else s
        out[s["ticker"]] = s.get("statements") or {}
    return out


def main(snap_dir: str) -> None:
    load_env()
    snaps = load_snapshots(snap_dir)
    month = date.today().isoformat()[:7]
    _, index = asyncio.run(_load_companyfacts(sorted(snaps), CACHE, month))
    rows, drift = [], []
    for t, st in sorted(snaps.items()):
        facts = read_companyfacts_cache(cik_for(t, index), cache_dir=CACHE, month=month)
        ends = st.get("fiscal_period_end") or []
        if facts is None or not ends:
            rows.append((t, "no-facts" if facts is None else "no-statements", "", None, None))
            continue
        for field in ("operating_cash_flow", "revenue", "net_income"):
            series = st.get(field) or []
            want = fy_value(facts, CONCEPTS[field], ends[0])
            got = series[0] if series else None
            status = ("ref-absent" if want is None else "MISSING" if got is None
                      else "MISMATCH" if off(got, want) else "ok")
            rows.append((t, field, status, got, want))
            for end, v in zip(ends, series, strict=False):
                w = fy_value(facts, CONCEPTS[field], end)
                if w is not None and off(v, w):
                    drift.append((t, field, end, v, w))
        fcf = (st.get("free_cash_flow") or [None])[0]
        ocf = fy_value(facts, CONCEPTS["operating_cash_flow"], ends[0])
        cx = fy_value(facts, CONCEPTS["capex"], ends[0])
        if None not in (fcf, ocf, cx):
            ref = ocf - cx
            rows.append((t, "fcf_sign", "ok" if (fcf < 0) == (ref < 0) else "SIGN-FLIP", fcf, ref))

    for k, n in sorted(Counter((f, s) for _, f, s, _, _ in rows).items()):
        print(k, n)
    print()
    for r in rows:
        if r[2] in ("MISMATCH", "MISSING", "SIGN-FLIP") or r[1] in ("no-facts", "no-statements"):
            print(r)
    print(f"\nall-years drift > {TOL:.0%}: {len(drift)}")
    for d in drift:
        print(d)


if __name__ == "__main__":
    main(sys.argv[1])
