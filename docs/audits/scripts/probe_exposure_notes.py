"""Which 10-K statement notes carry a company's exposures, and what do they cost?

For one ticker per process (so peak RSS is per ticker), lists EVERY note title of the
latest 10-K with its whitespace-collapsed size and content flags, then reports:

  Q1 selection - which titles the exposure family in `research/notes.py` selects, and
                 which it rejects or misses;
  Q2 payload   - does the text carry geographic revenue, a customer share, government
                 as customer, a commodity input or a currency exposure?
  Q3 cost      - peak RSS after the notes index loads and the debt family renders, and
                 again after the exposure family renders (the marginal cost).

Run one ticker at a time (sec.gov is shared with other jobs):
  uv run python docs/audits/scripts/probe_exposure_notes.py AMAT > /tmp/AMAT.json
"""
import json
import os
import re
import resource
import sys

from shortlist.env import load_env, redact_secrets

load_env()
from edgar import Company, set_identity  # noqa: E402  (identity must be set after load_env)

from shortlist.research import notes as notes_mod  # noqa: E402

set_identity(os.environ["SEC_IDENTITY"])

FLAGS = {
    "geography": re.compile(r"china|europe|asia|americas|united states|u\.s\.|"
                            r"international|geographic|foreign countr|region", re.I),
    "customer_share": re.compile(r"customers? (that )?accounted for|largest customer|"
                                 r"single customer|one customer|two customers|"
                                 r"\d+(\.\d+)?\s*%\s*of (our )?(total |net |consolidated )?"
                                 r"(net )?(revenue|sales)", re.I),
    "government": re.compile(r"u\.s\. government|federal government|department of "
                             r"defense|medicare|medicaid|government contract", re.I),
    "commodity": re.compile(r"commodit|crude|natural gas|steel|aluminum|copper", re.I),
    "currency": re.compile(r"foreign currenc|exchange rate", re.I),
}


def _rss_mb() -> float:
    return resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / 1024.0


def main(ticker: str) -> dict:
    rec: dict = {"ticker": ticker}
    filings = Company(ticker).get_filings(form="10-K")
    latest = next((f for f in filings if str(getattr(f, "form", "")) == "10-K"), None)
    if latest is None:
        return {**rec, "ERROR": "no 10-K"}
    rec["accession"] = str(latest.accession_no)
    tenk = latest.obj()
    rec["rss_after_parse_mb"] = round(_rss_mb(), 1)
    index = tenk.notes
    rec["n_notes"] = len(index)
    cfg = notes_mod.config_block(None)
    debt = notes_mod.collect(tenk, "10-K", rec["accession"], ticker, cfg)
    rec["debt_selected"] = [n.title for n in debt]
    rec["rss_after_debt_mb"] = round(_rss_mb(), 1)
    xcfg = notes_mod.exposure_config_block(None)
    exp = notes_mod.collect_exposures(tenk, rec["accession"], ticker, xcfg)
    rec["rss_after_exposure_mb"] = round(_rss_mb(), 1)
    rec["exposure_selected"] = [
        {"title": n.title, "chars": len(n.text), "truncated": n.truncated,
         **{k: bool(rx.search(n.text)) for k, rx in FLAGS.items()}}
        for n in exp]
    rows = []
    for i in range(len(index)):
        note = index[i]
        title = notes_mod._title(note)
        try:
            text = notes_mod._norm_ws(note.to_markdown())
        except Exception as e:
            rows.append({"title": title, "ERROR": redact_secrets(str(e))})
            continue
        rows.append({"title": title, "chars": len(text),
                     **{k: bool(rx.search(text)) for k, rx in FLAGS.items()}})
    rec["all_notes"] = rows
    return rec


if __name__ == "__main__":
    t = sys.argv[1].upper()
    try:
        out = main(t)
    except Exception as e:
        out = {"ticker": t, "ERROR": redact_secrets(f"{e.__class__.__name__}: {e}")}
    print(json.dumps(out, indent=1, default=str))
