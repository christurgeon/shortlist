"""Marginal cost of a harness-side adverse-controls producer, per ticker.

VERDICT 2026-09-07: NO-GO. +38% to +56% on a 120.2s ten-ticker /screen.
`docs/audits/2026-09-07-controls-scorecard-flag-cost.md`.

Measures exactly what a ScoreCard-flag producer would have to do that the harness
does NOT already do: select the latest exact-form 10-K (post-#199 rule) and pull the
whole-document text, then run the shipped detector. Section-only routes were closed
by the 2026-08-23 audit, so filing.text() is the only option.
"""
import datetime, os, sys, time
sys.path.insert(0, "src")
from shortlist.env import load_env
load_env()
from edgar import Company, set_identity
from shortlist.research import controls
from shortlist.research.filings import _latest_exact_10k

set_identity(os.environ["SEC_IDENTITY"])
CFG = controls.config_block(None)
tickers = open(sys.argv[1]).read().strip().split(",")

print(f"{'tk':6s} {'index_s':>8s} {'text_s':>8s} {'chars':>10s} {'detect_s':>9s} verdict")
tot_i = tot_t = tot_d = 0.0
for tk in tickers:
    try:
        t0 = time.time()
        filings = Company(tk).get_filings(form="10-K")
        f = _latest_exact_10k(filings)
        t1 = time.time()
        text = f.text()
        t2 = time.time()
        per = str(getattr(f, "period_of_report", "") or "")
        d = datetime.date.fromisoformat(per) if per else None
        found = controls.detect(text, d, CFG, form="10-K",
                                accession=str(getattr(f, "accession_no", "")))
        t3 = time.time()
        tot_i += t1 - t0; tot_t += t2 - t1; tot_d += t3 - t2
        print(f"{tk:6s} {t1-t0:8.2f} {t2-t1:8.2f} {len(text):10,d} {t3-t2:9.3f} "
              f"{'FLAG ' + found.basis if found else 'clean'}")
    except Exception as e:
        print(f"{tk:6s} ERROR {type(e).__name__}: {str(e)[:60]}")
n = len(tickers)
print(f"\nTOTALS over {n} tickers:")
print(f"  index selection : {tot_i:7.1f}s  ({tot_i/n:.2f}s/ticker)")
print(f"  filing.text()   : {tot_t:7.1f}s  ({tot_t/n:.2f}s/ticker)   <- the marginal cost")
print(f"  detect()        : {tot_d:7.3f}s  ({tot_d/n:.4f}s/ticker)  <- pure CPU, negligible")
print(f"  ADDED SERIAL    : {tot_i+tot_t+tot_d:7.1f}s")
