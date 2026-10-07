"""Feasibility probe behind the moat-durability pre-registration (2026-10-04).

Evidence for docs/audits/2026-10-04-moat-durability-prereg.md: the UNCONDITIONAL persistence of
a top-quintile ROIC, the effect of an invested-capital floor on it, the per-year universe sizes
the reproduction gate compares against, and whether companyfacts keeps filers that stopped
filing. Run from the repo root (about 110 keyless sec.gov requests, cached on disk):

    set -a && . ./.env && set +a
    uv run python docs/audits/scripts/probe_durability_frames.py

READ THIS BEFORE QUOTING A NUMBER FROM IT. SEC `frames` carries no `filed` date and returns the
latest RESTATED value for a period, so this is not point-in-time. It is good for a base rate
and for a plausibility band. It is NOT the verdict, and no predictor is tested here on purpose:
predictors were pre-registered first and are measured by probe_durability.py on companyfacts.

ROIC here is operating income x 0.79 / (equity + long-term debt + current debt), missing debt
= 0. The verdict's definition (shortlist/durability.py) does not add the current portion on top
of `LongTermDebt`. That is one reason the reproduction gate is a +/-15% band and not an
equality; the pre-registration (amendment 3, 3a) lists the others.
"""
import json
import os
import statistics
import sys
import urllib.request
from pathlib import Path

from shortlist.edgar.sec_throttle import sec_throttle
from shortlist.env import load_env, redact_secrets

CACHE = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(".cache/durability/frames")
YEARS = range(2011, 2026)
REV = ["RevenueFromContractWithCustomerExcludingAssessedTax", "Revenues", "SalesRevenueNet",
       "RevenueFromContractWithCustomerIncludingAssessedTax"]
# Filers that stopped filing. The names printed back are the check that each CIK is the firm
# intended. Aetna is an insurer with no OperatingIncomeLoss: a tag gap, expected.
DEAD = {"Linear Technology": 791907, "Monsanto": 1110783, "Celgene": 816284,
        "Time Warner": 1105705, "Aetna": 1122304, "Rockwell Collins": 1137411,
        "Red Hat": 1087423, "Xilinx": 743988, "LinkedIn": 1271024, "Google (old CIK)": 1288776}


def _get(url: str) -> dict:
    sec_throttle()("durability-frames")
    req = urllib.request.Request(url, headers={"User-Agent": os.environ["SEC_IDENTITY"]})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)


def frame(tag: str, period: str) -> dict:
    fn = CACHE / f"{tag}-{period}.json"
    if fn.exists():
        return json.loads(fn.read_text())
    try:
        d = _get(f"https://data.sec.gov/api/xbrl/frames/us-gaap/{tag}/USD/{period}.json")
    except Exception as e:                       # a 404 means no filer used the tag that period
        d = {"data": [], "_err": redact_secrets(str(e))[:80]}
    fn.write_text(json.dumps(d))
    return d


def by_cik(tags: list[str], period: str) -> dict[int, float]:
    out: dict[int, float] = {}
    for t in tags:                               # priority order, first wins
        for row in frame(t, period).get("data", []):
            out.setdefault(row["cik"], row["val"])
    return out


def universe(year: int, min_ic_frac: float) -> dict[int, float]:
    """{cik -> ROIC}: operating income and equity tagged, revenue >= $100M, invested capital
    > 0 and, when `min_ic_frac` is set, at least that share of total assets."""
    oi = by_cik(["OperatingIncomeLoss"], f"CY{year}")
    eq = by_cik(["StockholdersEquity"], f"CY{year}Q4I")
    ltd = by_cik(["LongTermDebtNoncurrent", "LongTermDebt"], f"CY{year}Q4I")
    cur = by_cik(["LongTermDebtCurrent", "DebtCurrent"], f"CY{year}Q4I")
    rev = by_cik(REV, f"CY{year}")
    assets = by_cik(["Assets"], f"CY{year}Q4I")
    out = {}
    for c, o in oi.items():
        if c not in eq or rev.get(c, 0) < 1e8:
            continue
        ic = eq[c] + ltd.get(c, 0) + cur.get(c, 0)
        if ic <= 0 or (min_ic_frac and (c not in assets or ic < min_ic_frac * assets[c])):
            continue
        out[c] = o * 0.79 / ic
    return out


def quintiles(roic: dict[int, float]) -> dict[int, int]:
    order = sorted(roic, key=roic.get, reverse=True)
    return {c: min(4, i * 5 // len(order)) for i, c in enumerate(order)}   # 0 = top quintile


def transitions(q: dict[int, dict[int, int]], h: int) -> tuple[list[float], int, dict[int, float]]:
    """Where a top-quintile name sits h years later: shares for Q1..Q5 and not-observed."""
    tot, n, per_year = [0] * 6, 0, {}
    for y in YEARS:
        if y + h not in q:
            continue
        top = [c for c, k in q[y].items() if k == 0]
        for c in top:
            tot[q[y + h].get(c, 5)] += 1
        n += len(top)
        per_year[y] = round(sum(q[y + h].get(c, 5) == 0 for c in top) / len(top), 3)
    return [x / n for x in tot], n, per_year


def main() -> None:
    load_env()
    if not os.environ.get("SEC_IDENTITY"):
        raise SystemExit("SEC_IDENTITY (a contact email) is required by the SEC")
    CACHE.mkdir(parents=True, exist_ok=True)

    print("== 1. unconditional persistence, no invested-capital floor")
    roic = {y: universe(y, 0.0) for y in YEARS}
    q = {y: quintiles(roic[y]) for y in YEARS}
    print("universe n by year:", {y: len(roic[y]) for y in YEARS})
    for h in (1, 3, 5):
        shares, n, per_year = transitions(q, h)
        print(f"h={h}y  Q1..Q5, not observed = {[round(s, 3) for s in shares]}  n={n}")
        print(f"       P(stay Q1) by start year: {per_year}")
    for y in (2015, 2020, 2024):
        v = sorted(roic[y].values(), reverse=True)
        print(f"{y}: Q1 floor {v[len(v) // 5]:.3f}, median {statistics.median(v):.3f}")

    print("\n== 2. the invested-capital floor (definition check; no predictor)")
    for frac in (0.0, 0.10, 0.25):
        r = {y: universe(y, frac) for y in YEARS}
        shares, n, _ = transitions({y: quintiles(r[y]) for y in YEARS}, 3)
        top = sorted(r[2024].values(), reverse=True)[: len(r[2024]) // 5]
        print(f"IC >= {frac:.2f} x assets: n2024={len(r[2024])} "
              f"Q1 max ROIC={top[0]:.1f} share>100%={sum(x > 1 for x in top) / len(top):.2f} | "
              f"3y stay={shares[0]:.3f} stay|observed={shares[0] / (1 - shares[5]):.3f} n={n}")

    print("\n== 3. reproduction-gate targets: universe n by year at the 0.10 floor")
    print({y: len(universe(y, 0.10)) for y in range(2011, 2025)})

    print("\n== 4. what the reproduction-gate targets count that a 10-K filter does not")
    # frames has no form field. A filer on forms 20-F or 40-F has a non-US address, so the share
    # of a target with no `US-` address (a blank one included) is close to an upper bound on
    # what a 10-K filter removes: many filers with a non-US address file a 10-K. Backs
    # amendment 3 of the pre-registration.
    for y in range(2011, 2025):
        target = universe(y, 0.10)
        loc = {row["cik"]: row.get("loc") or "" for row in
               frame("OperatingIncomeLoss", f"CY{y}").get("data", [])}
        abroad = sum(not loc[c].startswith("US-") for c in target)
        print(f"{y}: target {len(target)}, no US- address {abroad} ({abroad / len(target):.1%})")

    print("\n== 5. filers with ROIC inputs and none of the four revenue tags")
    # The universe needs revenue for its $100M floor. A filer that reports revenue under another
    # tag is outside it, and outside the reproduction targets too, so that gate cannot see the
    # hole. Backs amendment 4 of the pre-registration.
    for y in range(2011, 2025):
        oi = by_cik(["OperatingIncomeLoss"], f"CY{y}")
        eq = by_cik(["StockholdersEquity"], f"CY{y}Q4I")
        assets = by_cik(["Assets"], f"CY{y}Q4I")
        rev = by_cik(REV, f"CY{y}")
        have = [c for c in oi if c in eq and c in assets]
        none = [c for c in have if c not in rev]
        big = [c for c in have if assets[c] >= 5e8]
        big_none = sum(c not in rev for c in big)
        print(f"{y}: {len(have)} filers, no revenue tag {len(none)} ({len(none) / len(have):.1%}) | "
              f"assets >= $500M: {len(big)} filers, no revenue tag {big_none} "
              f"({big_none / len(big):.1%})")

    print("\n== 6. does companyfacts keep filers that stopped filing?")
    for name, cik in DEAD.items():
        try:
            d = _get(f"https://data.sec.gov/api/xbrl/companyfacts/CIK{cik:010d}.json")
        except Exception as e:
            print(f"{name:18s} ERROR {redact_secrets(str(e))[:80]}")
            continue
        rows = d.get("facts", {}).get("us-gaap", {}).get("OperatingIncomeLoss", {}).get(
            "units", {}).get("USD", [])
        ends = [f["end"] for f in rows if f.get("form") == "10-K"]
        print(f"{name:18s} -> {d.get('entityName')!r:32s} operating income on 10-K: "
              f"{min(ends) if ends else None} .. {max(ends) if ends else None}")


if __name__ == "__main__":
    main()
