"""Pure buyback-authorization aggregation over phrase-tagged EFTS rows (data/efts.py).

The discovery analogue of a "new repurchase authorization" 8-K: a filing whose full text
matches one of a curated set of verb-anchored authorization phrases (Ikenberry-Lakonishok-
Vermaelen 1995: +abnormal drift post-announcement; Peyer-Vermaelen 2009: persists OOS). A
CONTESTED-until-measured prior on the 8-K precedent — the academic sign justifies BUILDING
the originator, but the sign in THIS funnel's universe/horizon is what the pre-registered
backfill cohort (preregister/edgar_buyback.yaml) exists to measure, so the signal ships
DISABLED at weight 0.5 and supplies attention, not direction (the scorer + gates judge it).

Filter order mirrors eightk.py and is load-bearing: the `file_type != "8-K"` drop comes
FIRST (EFTS `forms=8-K` filters root_forms and returns 8-K/A amendment rows; an amendment
would double-fire the originator), then cross-phrase accession dedup, then the SPAC/SIC/
deny/suffix quality drops, then CIK->ticker resolution which ABSTAINS on a miss —
`display_names` is used ONLY as a name input to the SPAC check, NEVER as a ticker source.
The phrase-match already happened at fetch time (data/efts.fetch_phrase_*), so each row
carries the matched `phrase`; there is no item-set match here (unlike eightk.py).

The daily_cap + file_date-desc sort + seen-accession dedup are the SIGNAL's job
(EdgarBuybackSignal), matching the 8-K originator split — this leaf emits every matching
authorization so the tests can pin the pure filter/resolution contract.
"""
from __future__ import annotations

from typing import Callable, Optional

from .eightk import _junk_suffix
from .models import Emission
from .quality import is_spac_or_shell

SIGNAL = "edgar:buyback_auth"
# Verb-anchored authorization phrases (design §2.1) — each an EFTS EXACT phrase. Chosen to
# exclude "purchases under our existing share repurchase program" boilerplate; the measured
# precision is recorded in config.yaml (scout.buyback) + the PR. Config-overridable.
DEFAULT_PHRASES: tuple[str, ...] = (
    "authorized a new share repurchase program",
    "approved a new share repurchase program",
    "authorized a share repurchase program",
    "approved a share repurchase program",
    "authorized a new stock repurchase program",
    "increased its share repurchase program",
)
# Flat emission strength: authorization SIZE is not parseable from EFTS hit metadata without
# a per-document fetch (deferred), and the prior is contested — below the 13D/Form-4 marquee
# strengths, mirroring the FINRA/8-K precedent.
STRENGTH = 0.6
_SPAC_SIC = "6770"


def buyback_events_from_rows(rows: list[dict], *, resolve_ticker_fn: Callable,
                             deny_list=None, drop_spacs: bool = True) -> list[Emission]:
    """Phrase-tagged normalized EFTS rows -> buyback-authorization Emissions (one per
    accession; the SIGNAL applies file_date-desc sort + seen dedup + daily_cap on top).

    Pipeline (order is load-bearing — see module docstring): file_type != "8-K" drop ->
    cross-phrase accession dedup -> SIC-6770 blank-check drop -> SPAC/shell name drop ->
    resolver (abstain on miss) -> deny-list + 5th-letter junk-suffix drop -> per-ticker-per-
    day dedup (mirrors eightk.py — two same-day accessions for one issuer would otherwise
    double-emit and burn two daily_cap slots; the FIRST wins). Never raises on a bad row.
    Emission carries cik + meta={adsh, items, file_date, phrase} (the 8-K shape — the
    firehose + backfill join key need the CIK)."""
    deny = {str(d).upper() for d in (deny_list or [])}
    seen_acc: set[str] = set()
    seen_ticker_day: set[tuple[str, str]] = set()
    out: list[Emission] = []
    for r in rows:
        if (r.get("file_type") or "") != "8-K":
            continue                     # 8-K/A leak (root_forms) — mandatory, FIRST
        adsh = r.get("adsh") or ""
        if not adsh or adsh in seen_acc:
            continue                     # cross-phrase dedup: one emission per accession
        seen_acc.add(adsh)
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
            continue                     # one emission per ticker per day (mirrors eightk.py)
        seen_ticker_day.add((tkr, fday))
        phrase = str(r.get("phrase") or "")
        ev = f"8-K buyback authorization ('{phrase}') filed {fday}"
        out.append(Emission(tkr, SIGNAL, STRENGTH, ev, is_discovery=True,
                            cik=r.get("cik"),
                            meta={"adsh": adsh, "items": [str(i) for i in (r.get("items") or [])],
                                  "file_date": fday, "phrase": phrase}))
    return out
