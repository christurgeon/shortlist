"""Stress the 8-K design assumptions across a diverse ticker set."""
import os, re, sys, json
from shortlist.env import load_env
load_env()
from edgar import Company, set_identity
set_identity(os.environ["SEC_IDENTITY"])

TICKERS = ["AAPL", "MSFT", "JPM", "XOM", "TSLA", "WDC", "LLY", "NKE", "DIS", "CVX"]
PRIORITY = ("4.02", "2.02", "2.01", "1.01", "5.02")
EVENT_FORMS = ["8-K", "SC 13D", "SC 13G", "144", "SCHEDULE 13D", "SCHEDULE 13G", "10-Q", "10-K"]

out = {}
for t in TICKERS:
    rec = {"index40": {}, "eightks": []}
    try:
        # 1) What does the PRODUCTION index (40 rows, mixed forms) actually contain?
        idx = list(Company(t).get_filings(form=EVENT_FORMS))[:40]
        forms = {}
        for f in idx:
            forms[str(getattr(f, "form", "?"))] = forms.get(str(getattr(f, "form", "?")), 0) + 1
        rec["index40"] = forms
        rec["8k_in_index40"] = sum(1 for f in idx if str(getattr(f, "form", "")) == "8-K")

        # 2) items field: populated? format?
        eights = [f for f in Company(t).get_filings(form="8-K")][:6]
        for f in eights:
            items = getattr(f, "items", None)
            row = {"filed": str(f.filing_date), "items": items, "items_type": type(items).__name__}
            codes = re.findall(r"\d+\.\d+", str(items or ""))
            row["codes"] = codes
            row["priority_hit"] = [c for c in codes if c in PRIORITY]
            try:
                body = f.text() or ""
                row["body_len"] = len(body)
            except Exception as e:
                row["body_len"] = f"ERR {e.__class__.__name__}"
            exs = []
            try:
                for e in f.exhibits:
                    dt = str(getattr(e, "document_type", "") or "")
                    doc = str(getattr(e, "document", "") or "")
                    ln = None
                    if dt.upper().startswith("EX-"):
                        try:
                            ln = len(e.text() or "")
                        except Exception as err:
                            ln = f"ERR {err.__class__.__name__}"
                    exs.append({"type": dt, "doc": doc, "len": ln})
            except Exception as e:
                exs = [{"type": f"ERR {e.__class__.__name__}"}]
            row["exhibits"] = exs

            # 3) THE PREFIX-CAP TEST: where does guidance/outlook language sit?
            if "2.02" in codes:
                best = None
                for e in (f.exhibits or []):
                    if str(getattr(e, "document_type", "") or "").upper().startswith("EX-99"):
                        try:
                            txt = e.text() or ""
                        except Exception:
                            continue
                        if best is None or len(txt) > len(best):
                            best = txt
                if best:
                    norm = re.sub(r"\s+", " ", best)
                    hits = [(m.start(), round(m.start() / max(1, len(norm)), 2))
                            for m in re.finditer(r"outlook|guidance|we expect|expects? (?:to|revenue|full)", norm, re.I)]
                    row["ex_norm_len"] = len(norm)
                    row["raw_vs_norm"] = round(len(norm) / max(1, len(best)), 2)
                    row["guidance_hits"] = hits[:8]
                    row["guidance_first_frac"] = hits[0][1] if hits else None
            rec["eightks"].append(row)
    except Exception as e:
        rec["ERROR"] = f"{e.__class__.__name__}: {e}"
    out[t] = rec
    print(f"done {t}", file=sys.stderr)

print(json.dumps(out, indent=1, default=str))
