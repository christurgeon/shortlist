"""Stage 2: what does a 10-Q adverse-controls finding add over the SHIPPED 10-K path?

Reports TWO metrics, because the obvious one is wrong and the trap is the point.

**Naive (WRONG, kept deliberately):** flagged-on-any-10-Q-in-two-years minus
flagged-on-latest-10-K. This scored 9 of 11 on 2026-09-07 and is meaningless — a 10-Q
from 2024 against a 10-K covering 2026 measures REMEDIATION, not lead time. Every one
of the 9 had a clean 10-K whose period END was LATER than the flagged quarter.

**Correct:** a 10-Q buys lead time only when it covers a quarter the annual report does
NOT yet cover. So the case that matters is: the ticker's LATEST 10-Q flags, its period
end is AFTER the latest 10-K's period end, and that 10-K is clean. That is also what
`fetch_bundle` would actually see — it takes `get_filings(form="10-Q").latest(1)`.

Shipped 10-K semantics are replicated exactly: since #199 (2026-09-05)
`_fetch_10k_parsed` selects the newest **exact-form** 10-K and never a 10-K/A, so this
reads submissions JSON and does the same. Scanning a window of 10-Ks would credit the
shipped path with catches it cannot make; accepting amendments would read a document the
brief never sees.

The first run of this probe accepted 10-K/A rows (the pre-#199 rule, and the checkout it
ran against predated the fix). Re-checked: none of the 11 flagged tickers has an
amendment as its newest 10-K-family filing, so the verdict is identical either way. The
script reports `amendment_superseded` per ticker so a future corpus cannot hide the
difference — #199 measured 462 listed tickers currently in that state.

Reads tenq_controls_hits.json from stage 1. Throwaway probe.
"""
import datetime
import json
import pathlib
import sys
import time

SP = pathlib.Path(__file__).parent
sys.path.insert(0, str(SP))
import probe_tenq_controls as P


def latest_annual(cik):
    """(filed, accession, primary_doc, period_of_report, form, amendment_superseded)
    for the newest EXACT-FORM 10-K, matching `_latest_exact_10k` since #199.

    `amendment_superseded` is True when a 10-K/A is newer than the 10-K returned — the
    state #199 fixed, and the only shape in which the pre-#199 rule would have read a
    different document."""
    d = P._get(f"https://data.sec.gov/submissions/CIK{cik}.json", timeout=60)
    r = d["filings"]["recent"]
    exact, amended = None, None
    for i, form in enumerate(r["form"]):
        if form not in ("10-K", "10-K/A"):
            continue
        row = (r["filingDate"][i], r["accessionNumber"][i],
               r["primaryDocument"][i], r["reportDate"][i], form)
        if form == "10-K":
            if exact is None or row[0] > exact[0]:
                exact = row
        elif amended is None or row[0] > amended[0]:
            amended = row
    if exact is None:
        return None
    return exact + (bool(amended and amended[0] > exact[0]),)


def latest_quarterly_period(cik):
    d = P._get(f"https://data.sec.gov/submissions/CIK{cik}.json", timeout=60)
    r = d["filings"]["recent"]
    per = [r["reportDate"][i] for i, f in enumerate(r["form"]) if f == "10-Q"]
    return max(per) if per else None


def main():
    rows = json.loads((SP / "tenq_controls_hits.json").read_text())
    flagged_periods = {}
    for r in rows:
        if r["shipped"]:
            flagged_periods.setdefault(r["ticker"], []).append(r["period"])
    flagged = sorted(flagged_periods)
    print(f"{len(flagged)} tickers flagged on a 10-Q: {flagged}\n", flush=True)

    m = P._get("https://www.sec.gov/files/company_tickers.json")
    t2c = {v["ticker"].upper(): str(v["cik_str"]).zfill(10) for v in m.values()}

    out = []
    print(f"{'tk':6s} {'latest10K':11s} {'10K':6s} {'latest10Q':11s} {'lastFlagQ':11s} "
          f"{'uncovered':9s} latestQ_flags", flush=True)
    for tk in flagged:
        cik = t2c[tk]
        best = latest_annual(cik)
        if best is None:
            print(f"{tk:6s} no 10-K", flush=True)
            continue
        filed, adsh, doc, k_period, form, superseded = best
        text = P.document_text(cik, adsh, doc)
        per = datetime.date.fromisoformat(k_period) if k_period else None
        f = P.controls.detect(text, per, P.CFG, form=form, accession=adsh)
        lq = latest_quarterly_period(cik)
        uncovered = bool(lq and k_period and lq > k_period)
        latest_q_flags = lq in flagged_periods[tk]
        out.append({"ticker": tk, "form": form, "accession": adsh,
                    "tenk_period": k_period, "tenk_filed": filed,
                    "amendment_superseded": superseded,
                    "latest_tenq_period": lq, "uncovered_quarter": uncovered,
                    "latest_tenq_flags": latest_q_flags,
                    "flagged_quarters": sorted(flagged_periods[tk]),
                    "tenk": None if f is None else
                            {"basis": f.basis, "as_of": f.as_of, "quote": f.quote}})
        print(f"{tk:6s} {k_period:11s} {'FLAG' if f else 'clean':6s} {str(lq):11s} "
              f"{max(flagged_periods[tk]):11s} {str(uncovered):9s} {latest_q_flags}",
              flush=True)
        time.sleep(0.25)

    (SP / "tenq_incremental.json").write_text(json.dumps(out, indent=1))

    naive = [r["ticker"] for r in out if not r["tenk"]]
    lead = [r["ticker"] for r in out
            if r["uncovered_quarter"] and r["latest_tenq_flags"] and not r["tenk"]]
    print("\n=== NAIVE metric (WRONG — measures remediation) ===")
    print(f"flagged on some 10-Q, latest 10-K clean : {len(naive)}  {naive}")
    print("\n=== CORRECT metric (lead time) ===")
    print(f"uncovered quarter exists                : "
          f"{sum(1 for r in out if r['uncovered_quarter'])}")
    print(f"  ...and the latest 10-Q flags          : "
          f"{[r['ticker'] for r in out if r['uncovered_quarter'] and r['latest_tenq_flags']]}")
    print(f"  ...and the latest 10-K is clean       : {len(lead)}  {lead}")
    sup = [r["ticker"] for r in out if r["amendment_superseded"]]
    print(f"\ntickers with a NEWER 10-K/A than the 10-K read (pre-#199 divergence): "
          f"{len(sup)}  {sup}")
    print(f"\nINCREMENTAL LEAD-TIME YIELD: {len(lead)} tickers")


if __name__ == "__main__":
    main()
