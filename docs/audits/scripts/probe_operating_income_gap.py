"""Reproduce the 2026-08-21 `operating_income` gap diagnosis (TODO.md §3).

Two independent passes, either runnable alone:

    uv run python docs/audits/scripts/probe_operating_income_gap.py store
    uv run python docs/audits/scripts/probe_operating_income_gap.py live

`store` scans the accumulate snapshot store and cross-tabs `operating_income`
presence against whether FMP won the statements merge. It reads BOTH `.json` and
`.json.gz` — the store gzips snapshots after a few days, and a `.json`-only scan
silently samples the first two weeks and reports a ~0% gap instead of ~25%.

`live` asks SEC two questions per ticker that the store cannot answer: does the
rendered income statement carry `us-gaap_OperatingIncomeLoss` at all, and does
`companyconcept` carry it when the statement does not. Needs SEC_IDENTITY.
"""
import collections
import gzip
import json
import os
import sys
import time
import urllib.request

STORE = os.environ.get("SNAPSHOT_STORE", "/opt/shortlist/state/snapshots")

# The 2026-08-21 populations. Kept as literals so a re-run that reshuffles them is
# visible as a diff rather than silently absorbed into a recomputed grouping.
BANKS = ["BAC", "GS", "WFC", "JPM"]
TAG_ABSENT = ["XOM", "IBM", "LLY", "MRK", "CVX", "NKE"]
TAG_STALE = ["JNJ"]
TAG_CURRENT = ["HON", "DIS"]


def _load(path):
    opener = gzip.open if path.endswith(".gz") else open
    with opener(path, "rt") as fh:
        return json.load(fh)


def _snapshots():
    for ticker in sorted(os.listdir(STORE)):
        directory = os.path.join(STORE, ticker)
        if not os.path.isdir(directory):
            continue
        for name in sorted(os.listdir(directory)):
            if not name.endswith((".json", ".json.gz")):
                continue
            yield ticker, name.split(".")[0], _load(os.path.join(directory, name))


def store_pass():
    xtab = collections.Counter()
    missing = collections.Counter()
    edgar_only = 0
    total = 0
    for ticker, _date, snap in _snapshots():
        statements = snap.get("statements") or {}
        provenance = (snap.get("provenance") or {}).get("statements") or []
        fmp_won = "fmp" in provenance
        present = bool(statements.get("operating_income"))
        xtab[(fmp_won, present)] += 1
        total += 1
        if not fmp_won:
            edgar_only += 1
            if not present:
                missing[ticker] += 1

    print(f"snapshots: {total}   EDGAR-only: {edgar_only}   fmp-won: {total - edgar_only}")
    print(f"fmp-won    -> operating_income present {xtab[(True, True)]}, absent {xtab[(True, False)]}")
    print(f"EDGAR-only -> operating_income present {xtab[(False, True)]}, absent {xtab[(False, False)]}")
    print()
    gap = sum(missing.values())
    whole_run = [t for t in missing if t not in BANKS + TAG_ABSENT + TAG_STALE + TAG_CURRENT]
    for label, group in (("whole-statements-run failure", whole_run),
                         ("banks, correctly uncomputable", BANKS),
                         ("tag genuinely absent from XBRL", TAG_ABSENT),
                         ("tag present but stale", TAG_STALE),
                         ("tag present and current", TAG_CURRENT)):
        n = sum(missing[t] for t in group)
        print(f"  {label:34} {n:4}  {sorted(group)}")
    print(f"  {'TOTAL':34} {gap:4}")


def _get(url, identity):
    request = urllib.request.Request(url, headers={"User-Agent": identity})
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            return json.load(response)
    except Exception as exc:                      # 404 == the filer never tags the concept
        return {"__err": f"{type(exc).__name__} {getattr(exc, 'code', '')}"}


def live_pass():
    from shortlist.env import load_env
    load_env()
    identity = os.environ["SEC_IDENTITY"]
    from edgar import Company, set_identity
    set_identity(identity)

    tickers = _get("https://www.sec.gov/files/company_tickers.json", identity)
    cik_by_ticker = {v["ticker"]: v["cik_str"] for v in tickers.values()}

    for ticker in TAG_ABSENT + TAG_STALE + TAG_CURRENT + BANKS + ["AAPL", "MSFT"]:
        frame = Company(ticker).get_financials().income_statement().to_dataframe()
        concepts = frame["concept"].astype(str) if "concept" in frame.columns else []
        on_statement = sum(1 for c in concepts if c == "us-gaap_OperatingIncomeLoss")

        cik = cik_by_ticker[ticker]
        facts = _get("https://data.sec.gov/api/xbrl/companyconcept/"
                     f"CIK{int(cik):010d}/us-gaap/OperatingIncomeLoss.json", identity)
        time.sleep(0.15)
        if "__err" in facts:
            print(f"{ticker:6} statement={on_statement}  companyconcept -> {facts['__err']}")
            continue
        annual = [u for u in facts.get("units", {}).get("USD", [])
                  if u.get("form") == "10-K" and u.get("start") and u.get("end")
                  and (int(u["end"][:4]) * 12 + int(u["end"][5:7]))
                  - (int(u["start"][:4]) * 12 + int(u["start"][5:7])) >= 11]
        last_filed = max((u.get("filed", "") for u in annual), default="-")
        newest = max((u["end"] for u in annual), default="-")
        print(f"{ticker:6} statement={on_statement}  companyconcept facts={len(annual):3} "
              f"newest FY end={newest}  last filed={last_filed}")


if __name__ == "__main__":
    which = sys.argv[1] if len(sys.argv) > 1 else "store"
    (store_pass if which == "store" else live_pass)()
