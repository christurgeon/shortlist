"""Wide probe: how often does 10-Q Part I Item 2 (MD&A) extraction fail, and when it
does, is the "recover from the containing item" heuristic a RULE or a coincidence?

Resumable: appends one JSON object per line to wide.jsonl, skipping tickers already
present. Holds no parsed filing beyond the current ticker (1.9GB box, live bot).
"""
import gc
import json
import os
import re
import sys
from pathlib import Path

from shortlist.env import load_env

load_env()
from edgar import Company, set_identity  # noqa: E402

set_identity(os.environ["SEC_IDENTITY"])

OUT = Path(os.environ.get("PROBE_OUT", "/tmp/probe_10q_mda.jsonl"))
HEAD = re.compile(r"management['’]s discussion and analysis", re.I)
REPO = Path(__file__).resolve().parents[3] / "src" / "shortlist" / "backtest"


def universe():
    seen, out = set(), []
    for name, tag in (("universe_largecap.txt", "large"), ("universe_smallmid.txt", "smallmid")):
        for line in (REPO / name).read_text().splitlines():
            t = line.strip()
            if not t or t.startswith("#") or t in seen:
                continue
            seen.add(t)
            out.append((t, tag))
    return out


def done_set():
    if not OUT.exists():
        return set()
    return {json.loads(ln)["ticker"] for ln in OUT.read_text().splitlines() if ln.strip()}


def probe(ticker, tag):
    row = {"ticker": ticker, "cap": tag}
    f = Company(ticker).get_filings(form="10-Q").latest(1)
    if f is None:
        row["err"] = "no 10-Q"
        return row
    row["accession"] = str(getattr(f, "accession_no", ""))
    o = f.obj()
    if o is None:
        row["err"] = "obj() is None"
        return row
    mda = str(o.get_item_with_part("Part I", "Item 2", markdown=True) or "")
    row["mda"] = len(mda)
    try:
        doc = str(o.doc or "")
    except Exception:
        doc = ""
    row["doc"] = len(doc)
    row["frac"] = round(len(mda) / len(doc), 3) if doc and mda else None
    if not mda:
        # THE decisive data: for a failing name, where could MD&A be recovered from,
        # and is "the last heading hit" the right pick or an INTC-specific fluke?
        blob = str(o.get_item_with_part("Part I", "Item 1", markdown=True) or "")
        row["item1"] = len(blob)
        hits = [m.start() for m in HEAD.finditer(blob)]
        row["hits"] = len(hits)
        row["hit_fracs"] = [round(h / len(blob), 3) for h in hits] if blob else []
        # what each hit looks like, so a human can judge glossary vs real heading
        row["hit_ctx"] = [" ".join(blob[max(0, h - 60):h + 160].split())[:200] for h in hits[:6]]
        row["tail_from_last"] = len(blob) - hits[-1] if hits else 0
    return row


def main():
    todo = [(t, c) for t, c in universe() if t not in done_set()]
    print(f"{len(todo)} tickers to go", file=sys.stderr)
    with OUT.open("a") as fh:
        for i, (t, c) in enumerate(todo, 1):
            try:
                row = probe(t, c)
            except Exception as e:
                row = {"ticker": t, "cap": c, "err": f"{type(e).__name__}: {str(e)[:120]}"}
            fh.write(json.dumps(row) + "\n")
            fh.flush()
            gc.collect()
            if i % 10 == 0:
                print(f"  {i}/{len(todo)}", file=sys.stderr)


if __name__ == "__main__":
    main()
