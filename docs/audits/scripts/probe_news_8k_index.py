"""List each ticker's 8-Ks in the news window (filed date + item codes)."""
import json, os, sys, time
from datetime import date, timedelta
from shortlist.env import load_env
load_env()
from edgar import Company, set_identity
set_identity(os.environ["SEC_IDENTITY"])
out = {}
since = date.today() - timedelta(days=45)
for t in sys.argv[2:]:
    try:
        fl = Company(t).get_filings(form="8-K")
        rows = []
        for f in fl.head(15):
            fd = f.filing_date if isinstance(f.filing_date, date) else date.fromisoformat(str(f.filing_date))
            if fd < since:
                break
            rows.append({"filed": fd.isoformat(), "items": str(getattr(f, "items", "") or ""), "acc": f.accession_no})
        out[t] = rows
        print(t, rows)
    except Exception as e:
        print(t, "ERROR", type(e).__name__, str(e)[:120])
    time.sleep(0.3)
json.dump(out, open(sys.argv[1], "w"), indent=1)
