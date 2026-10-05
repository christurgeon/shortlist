"""Instrument check for a 10-K competition-word count (2026-10-04). Evidence for the
"excluded before registration" line in docs/audits/2026-10-04-moat-durability-prereg.md.

    set -a && . ./.env && set +a
    uv run --extra edgar python docs/audits/scripts/probe_pctcomp_instrument.py V MA MSFT KO \\
        MCO SPGI FICO WMT DAL AAL F INTC NVDA PTON DG CMG VRSN ODFL

Li, Lundholm, Minnis (2013): PCTCOMP = 1000 * NCOMP / NWORDS, where NCOMP counts competition /
competitor / competitive / compete / competing (and plurals) unless "not", "less", "few" or
"limited" precedes within three words. They find a higher count goes with faster mean reversion
of returns on net operating assets.

The ONLY question asked here: does the number spread across firms, or does it compress the way
research/textsim.py's cosine did? No outcome is tested. Result on the 18 names above: it
spreads about 5x (1.09 to 5.40), but the cross-industry order has no face validity (Ford and
Delta read as LOW competition, Microsoft as high), and it moves with extraction (INTC's Item 1
came back empty, which shrank the denominator). n=18 is a sanity check on this instrument over
three sections, not a verdict on the paper, which used the whole document and industry controls.
"""
import re
import sys

from shortlist.env import load_env
from shortlist.research.filings import fetch_10k

_COMP = re.compile(r"^(competition|competitor|competitive|compete|competing)s?$")
_NEG = {"not", "less", "few", "limited"}


def pctcomp(text: str) -> tuple[float | None, int, int]:
    toks = re.findall(r"[A-Za-z]+", text.lower())
    n = sum(1 for i, t in enumerate(toks)
            if _COMP.match(t) and not (_NEG & set(toks[max(0, i - 3):i])))
    return (1000 * n / len(toks) if toks else None), n, len(toks)


def main() -> None:
    load_env()
    rows = []
    for tk in sys.argv[1:]:
        try:
            ft = fetch_10k(tk)
        except Exception as e:
            print(tk, "ERROR", type(e).__name__)
            continue
        if ft is None:
            print(tk, "no 10-K")
            continue
        score, n, words = pctcomp(" ".join(s or "" for s in (ft.business, ft.risk_factors, ft.mda)))
        business = pctcomp(ft.business or "")[0]
        rows.append((score, tk))
        print(f"{tk:6s} PCTCOMP={score:5.2f} ncomp={n:4d} words={words:6d} "
              f"business-only={None if business is None else round(business, 2)}")
    print("sorted:", [(t, round(p, 2)) for p, t in sorted(rows)])


if __name__ == "__main__":
    main()
