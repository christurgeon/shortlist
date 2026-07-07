"""Pure 8-K aggregation over normalized EFTS rows (data/efts.py).

Two halves, one feed (design 2026-07-07):
- POSITIVE POCKET (edgar:8k): filings whose items contain a configured AND-set (default
  1.01∧3.03 — Lerman-Livnat 2010's only positive-drift pocket). A CONTESTED prior: Zhao
  2017's unconditional 8-K sign is negative and filing-day moves reverse, so the signal
  ships disabled at weight 0.5 and supplies attention, not direction.
- NEGATIVE SET (edgar:8k_negative): items {1.03, 2.04, 2.05, 2.06, 3.01, 4.02, 5.01} —
  reliably negative 30-90d drift. Extraction here is deliberately BROAD (no SPAC/SIC/
  suffix drops): a blank-check bankruptcy still vetoes.

Filter order is load-bearing: the `file_type != "8-K"` drop comes FIRST (EFTS `forms=8-K`
filters root_forms and returns 8-K/A amendment rows — live-verified; an amendment would
double-fire the originator and re-trigger the veto). CIK->ticker resolution ABSTAINS on a
miss — `display_names` is used only as a name input to the SPAC check, NEVER as a ticker
source (mis-binding risk; matches the backfill rule).
"""
from __future__ import annotations

from typing import Callable, Optional

from .delisting import normalize_items
from .models import Emission
from .quality import is_spac_or_shell
from .short_interest import _FIFTH_LETTER_SUFFIXES

SIGNAL = "edgar:8k"
NEGATIVE_SIGNAL = "edgar:8k_negative"
# Lerman-Livnat 2010 negative-drift items (see the design spec §1/§4).
NEGATIVE_ITEMS = frozenset({"1.03", "2.04", "2.05", "2.06", "3.01", "4.02", "5.01"})
# The curated positive pocket: 1.01 (material agreement) AND 3.03 (security-holder
# rights). 2.01 is config-expressible but NOT in the default set.
DEFAULT_ITEM_SETS: tuple[tuple[str, ...], ...] = (("1.01", "3.03"),)
# Fixed emission strength: the pocket is binary (a filing matches or it doesn't) and the
# prior is contested — below the 13D/Form-4 marquee strengths, like the FINRA precedent.
STRENGTH = 0.6
_SPAC_SIC = "6770"


def match_item_sets(items, item_sets) -> Optional[list[str]]:
    """Sorted union of matched items when ANY configured AND-set is fully contained in
    `items`; None otherwise. Sets are ANDed internally, ORed across the list."""
    present = set(items)
    matched: set[str] = set()
    for s in item_sets:
        want = {str(x) for x in s}
        if want and want <= present:
            matched.update(want)
    return sorted(matched) if matched else None


def match_negative(items) -> Optional[list[str]]:
    hit = NEGATIVE_ITEMS & set(items)
    return sorted(hit) if hit else None


def _junk_suffix(ticker: str) -> bool:
    """5th-letter security-type suffix on a 5-letter symbol (F/Y/W/U/R/Q — the FINRA
    originator's rule, single-sourced from short_interest.py). 4-char tickers ending in
    these letters (WOOF) are fine; dotted/hyphenated share classes are NOT dropped."""
    return len(ticker) == 5 and ticker.isalpha() and ticker[-1] in _FIFTH_LETTER_SUFFIXES


def eightk_events_from_rows(rows: list[dict], *, resolve_ticker_fn: Callable,
                            item_sets=None, deny_list=None,
                            drop_spacs: bool = True) -> list[Emission]:
    """Normalized EFTS rows -> positive-pocket Emissions (one per ticker per file_date).

    Pipeline (order is load-bearing — see module docstring): file_type != "8-K" drop ->
    accession dedup -> item AND-set match -> SIC-6770 blank-check drop -> SPAC/shell name
    drop -> resolver (abstain on miss) -> deny-list + 5th-letter junk-suffix drop ->
    per-ticker-per-day dedup. Never raises on a bad row."""
    item_sets = [tuple(s) for s in (item_sets or DEFAULT_ITEM_SETS)]
    deny = {str(d).upper() for d in (deny_list or [])}
    seen_acc: set[str] = set()
    seen_ticker_day: set[tuple[str, str]] = set()
    out: list[Emission] = []
    for r in rows:
        if (r.get("file_type") or "") != "8-K":
            continue                     # 8-K/A leak (root_forms) — mandatory, FIRST
        adsh = r.get("adsh") or ""
        if not adsh or adsh in seen_acc:
            continue
        seen_acc.add(adsh)
        matched = match_item_sets(normalize_items(r.get("items")), item_sets)
        if matched is None:
            continue
        if _SPAC_SIC in (r.get("sics") or []):
            continue                     # blank-check SIC — free, inline in the EFTS hit
        names = r.get("display_names") or []
        if drop_spacs and names and is_spac_or_shell(str(names[0])):
            continue                     # name check ONLY — never a ticker source
        tkr = resolve_ticker_fn(r.get("cik"))
        if not tkr:
            continue                     # resolver abstention: NO display_names fallback
        tkr = str(tkr).upper()
        if tkr in deny or _junk_suffix(tkr):
            continue
        fday = r.get("file_date") or ""
        if (tkr, fday) in seen_ticker_day:
            continue                     # one emission per ticker per day
        seen_ticker_day.add((tkr, fday))
        ev = f"8-K items {'+'.join(matched)} filed {fday}"
        out.append(Emission(tkr, SIGNAL, STRENGTH, ev, is_discovery=True,
                            cik=r.get("cik"),
                            meta={"adsh": adsh, "items": matched, "file_date": fday}))
    return out


def negative_events_from_rows(rows: list[dict], *,
                              resolve_ticker_fn: Callable) -> list[dict]:
    """Normalized EFTS rows -> negative-item veto records
    `{"ticker","cik","adsh","file_date","items"}`. Broad by design: file_type drop +
    accession dedup + item match + resolver abstention ONLY (no quality drops — a junky
    name that never reaches the funnel is simply an inert map entry)."""
    seen_acc: set[str] = set()
    out: list[dict] = []
    for r in rows:
        if (r.get("file_type") or "") != "8-K":
            continue
        adsh = r.get("adsh") or ""
        if not adsh or adsh in seen_acc:
            continue
        seen_acc.add(adsh)
        matched = match_negative(normalize_items(r.get("items")))
        if matched is None:
            continue
        tkr = resolve_ticker_fn(r.get("cik"))
        if not tkr:
            continue                     # abstain — an unresolvable filer can't be in the funnel
        out.append({"ticker": str(tkr).upper(), "cik": r.get("cik"), "adsh": adsh,
                    "file_date": r.get("file_date") or "", "items": matched})
    return out
