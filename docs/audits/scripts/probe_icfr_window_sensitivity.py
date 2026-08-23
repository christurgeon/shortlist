"""Q2: window (chars) and tolerance (days) are UNFITTED PRIORS I chose by eye.
Are they on a cliff edge? Sweep both over the decisive corpus."""
import os, sys, re, datetime, json, pathlib
sys.path.insert(0, "/tmp/claude-1000/-home-chris-shortlist/8518e3ec-608c-4017-9989-04760019e244/scratchpad")
os.environ.setdefault("SEC_IDENTITY", "shortlist-research turgechr@duck.com")
import rule
from edgar import Company, set_identity
set_identity(os.environ["SEC_IDENTITY"])
norm = lambda s: re.sub(r"[\s ]+", " ", str(s or ""))
SP = pathlib.Path("/tmp/claude-1000/-home-chris-shortlist/8518e3ec-608c-4017-9989-04760019e244/scratchpad")

CORPUS = [  # (ticker, accession, period, truth)
 ("CASH","0000907471-25-000116","2025-09-30",1), ("CASH","0000907471-25-000083","2024-09-30",1),
 ("CMP","0001227654-25-000199","2025-09-30",1),  ("CWH","0001558370-25-001939","2024-12-31",1),
 ("GIII","0001104659-26-033891","2026-01-31",1), ("GRBK","0001628280-26-033478","2025-12-31",1),
 ("HP","0000046765-25-000071","2025-09-30",1),   ("JJSF","0001437749-24-036279","2024-09-28",1),
 ("NEOG","0000950170-25-100064","2025-05-31",1), ("NSSC","0001558370-24-012547","2024-06-30",1),
 ("SMP","0000093389-26-000012","2025-12-31",1),  ("UCTT","0001628280-25-007865","2024-12-27",1),
 ("TALO","0000950170-24-125528","2023-12-31",1),
 ("JJSF","0001437749-25-036456","2025-09-27",0), ("USNA","0000896264-26-000021","2026-01-03",0),
 ("USNA","0000896264-25-000092","2024-12-28",0), ("CENT","0000887733-24-000029","2024-09-28",0),
 ("CWH","0001104659-26-021548","2025-12-31",0),  ("UCTT","0001628280-26-010744","2025-12-26",0),
 ("NSSC","0001558370-25-011750","2025-06-30",0), ("SPGI","0000064040-26-000013","2025-12-31",0),
 ("HMN","0000850141-26-000007","2025-12-31",0),  ("VITL","0001193125-26-073423","2025-12-28",0),
]
cache = SP / "sens_texts.json"
texts = json.loads(cache.read_text()) if cache.exists() else {}
for tk, acc, pe, truth in CORPUS:
    if acc in texts: continue
    try:
        f = next(x for x in Company(tk).get_filings(form="10-K") if getattr(x, "accession_no", "") == acc)
        texts[acc] = norm(f.text())
        print(f"  fetched {tk} {acc} ({len(texts[acc])} chars)", flush=True)
    except Exception as e:
        print(f"  ! {tk} {acc}: {type(e).__name__}: {e}", flush=True)
cache.write_text(json.dumps(texts))

print(f"\ncorpus: {len(texts)} filings ({sum(t for *_ , t in CORPUS)} positive)\n")
print(f"{'window':>7} " + "".join(f"{'tol='+str(t):>12}" for t in (7, 20, 45, 90, 200)))
for w in (100, 160, 240, 400, 800, 1600):
    cells = []
    for tol in (7, 20, 45, 90, 200):
        tp = fp = fn = tn = 0
        for tk, acc, pe, truth in CORPUS:
            if acc not in texts: continue
            pred = rule.is_current_adverse(texts[acc], datetime.date.fromisoformat(pe), window=w, tol_days=tol)[0]
            tp += pred and truth; fp += pred and not truth
            fn += (not pred) and truth; tn += (not pred) and (not truth)
        cells.append(f"{tp}/{fp}/{fn}".rjust(12))
    print(f"{w:>7} " + "".join(cells))
print("\ncells are tp/fp/fn; ideal is 13/0/0")
