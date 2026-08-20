"""Stress the debt & liquidity note design assumptions across a diverse ticker set.

Answers the four questions the design rests on:
  Q1 selection  - does a title regex find the debt note, and how often is there
                  more than one? (10-K and 10-Q separately)
  Q2 size       - how big is to_markdown(), before and after whitespace collapse?
                  (decides the char caps)
  Q3 payload    - does the extracted text actually carry maturities / facilities /
                  covenants, i.e. the inputs the SYSTEM_PROMPT arithmetic asks for?
  Q4 truncation - where does the LAST table row boundary fall relative to a cap?
                  (a prefix cut through a table row would present a severed number)

Run:  uv run python docs/audits/scripts/probe_debt_notes.py > /tmp/debt_notes.json
"""
import json
import os
import re
import sys

from shortlist.env import load_env

load_env()
from edgar import Company, set_identity  # noqa: E402  (identity must be set after load_env)

set_identity(os.environ["SEC_IDENTITY"])

# Deliberately mixed: mega-cap tech, a bank, energy, pharma, retail, an airline,
# a REIT, a utility, a heavy borrower and a debt-free biotech (the true negative).
TICKERS = ["AAPL", "MSFT", "JPM", "XOM", "LLY", "NKE", "INTC", "T", "BA", "CVS",
           "UAL", "MRNA", "O", "DUK", "F", "KO", "GS", "AMT", "TGT", "PLTR"]

TITLE_RE = re.compile(
    r"debt|borrow|credit facilit|credit agreement|financing arrangement|notes payable"
    r"|long[- ]term obligation", re.I)

# Negative filter. "Investments in Debt and Equity Securities" (DUK) is an ASSET
# note that matches `debt`; unfiltered it consumed 10,127 chars of the budget.
# `long[- ]term obligation` is deliberately narrow so "ASSET RETIREMENT
# OBLIGATIONS" (AMT note 9) does not match while "LONG-TERM OBLIGATIONS"
# (AMT's actual debt note, note 8) does.
EXCLUDE_RE = re.compile(r"investment|marketable securit|available[- ]for[- ]sale", re.I)

MATUR_RE = re.compile(r"matur", re.I)
FACIL_RE = re.compile(r"revolv|credit facilit|undrawn|unused|remaining borrowing", re.I)
COV_RE = re.compile(r"covenant", re.I)

CAP = 10000          # candidate max_chars_per_note


def _title(note) -> str:
    return str(getattr(note, "title", None) or getattr(note, "name", "") or "")


def _probe_form(ticker: str, form: str) -> dict:
    rec: dict = {"matched": [], "n_notes": None}
    filing = Company(ticker).get_filings(form=form).latest(1)
    if filing is None:
        rec["ERROR"] = "no filing"
        return rec
    rec["accession"] = str(getattr(filing, "accession_no", "") or "")
    notes = filing.obj().notes
    rec["n_notes"] = len(notes)
    for i in range(len(notes)):
        note = notes[i]
        title = _title(note)
        if not TITLE_RE.search(title) or EXCLUDE_RE.search(title):
            continue
        raw = str(note.to_markdown())
        norm = re.sub(r"[ \t]+", " ", raw)
        norm = re.sub(r"\n{3,}", "\n\n", norm).strip()
        row = {
            "title": title,
            "raw_chars": len(raw),
            "norm_chars": len(norm),
            "shrink": round(len(norm) / max(1, len(raw)), 2),
            "has_maturities": bool(MATUR_RE.search(norm)),
            "has_facility": bool(FACIL_RE.search(norm)),
            "has_covenant": bool(COV_RE.search(norm)),
            "n_tables": norm.count("#### Table:"),
            "over_cap": len(norm) > CAP,
        }
        if row["over_cap"]:
            # Q4: a prefix cut must never sever a number mid-digits ("4,100" ->
            # "4,1"). Compare the two candidate cut points by how much of the cap
            # each wastes: the last newline (row-aligned) vs the last whitespace
            # (token-aligned). GS is the stress case — its note has very long
            # lines, so row alignment throws away far more of the budget.
            head = norm[:CAP]
            nl, ws = head.rfind("\n"), max(head.rfind(" "), head.rfind("\n"))
            row["waste_row_align"] = CAP - nl if nl > 0 else None
            row["waste_token_align"] = CAP - ws if ws > 0 else None
        rec["matched"].append(row)
    return rec


out: dict = {}
for t in TICKERS:
    rec: dict = {}
    for form in ("10-K", "10-Q"):
        try:
            rec[form] = _probe_form(t, form)
        except Exception as e:
            rec[form] = {"ERROR": f"{e.__class__.__name__}: {e}"}
    out[t] = rec
    n10k = len(rec.get("10-K", {}).get("matched", []) or [])
    n10q = len(rec.get("10-Q", {}).get("matched", []) or [])
    print(f"done {t}  10-K matches={n10k}  10-Q matches={n10q}", file=sys.stderr)

print(json.dumps(out, indent=1, default=str))
