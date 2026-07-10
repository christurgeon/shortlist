"""Delisting detect + reason classifier for the Phase-2 backfill (sign-integrity, spec §6.6).

Detection: a Form 25/25-NSE (delisting notice) or Form 15 (deregistration) for the subject CIK.
Reason from structured 8-K item codes in the window before the delisting (no NLP):
Item 1.03 -> bankruptcy (Shumway venue partial: NYSE -30% / Nasdaq -55%); Item 2.01 + 5.01 in the
SAME 8-K -> M&A/take-private (terminal = last traded close, NOT a penalty); neither -> unclassified
-> non-measurable (never a guessed sign). Precedence (R-B3): a 1.03 anywhere in the window
overrides a later 2.01+5.01 (post-Ch.11 asset sales are bankruptcy artifacts).

Pure classification core over lightweight FilingRecords + a thin CIK-keyed edgartools fetcher
(the edgar Company lookup is ALWAYS int-cast from the subject CIK, never a ticker — the
BBBY->Overstock ticker-reuse landmine). Never raises to the caller.
See docs/superpowers/specs/2026-07-01-signal-validation-harness-backfill-design.md §6.6/§8/§12/§16.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import date, timedelta
from typing import Iterable, Optional

BANKRUPTCY = "bankruptcy"
MNA = "mna"
UNCLASSIFIED = "unclassified"

_ITEM_RE = re.compile(r"\d+\.\d{2}")
# Shumway (1997) delisting-bias partials by listing venue (spec §6.6). Unknown venue -> the
# harsher Nasdaq figure (conservative for a long book; the sensitivity band re-stresses anyway).
_SHUMWAY = {"nyse": -0.30, "nasdaq": -0.55}


def _base_form(form) -> str:
    """'25-NSE/A' -> '25-NSE'; None-safe."""
    return str(form or "").split("/")[0].strip().upper()


def normalize_items(raw) -> tuple[str, ...]:
    """Extract 8-K item codes ('1.03', '2.01', ...) from whatever shape the source hands us:
    the submissions-JSON comma string, edgartools' labelled list, or junk (-> ()). Never raises."""
    if raw is None:
        return ()
    if isinstance(raw, str):
        parts: Iterable = [raw]
    else:
        try:
            parts = list(raw)
        except TypeError:
            return ()
    out: list[str] = []
    for p in parts:
        for code in _ITEM_RE.findall(str(p)):
            if code not in out:
                out.append(code)
    return tuple(out)


def venue_from_filer(name) -> Optional[str]:
    """'nyse' | 'nasdaq' | None from a Form-25 filer name (the exchange files Form 25)."""
    n = str(name or "").lower()
    if "nasdaq" in n:
        return "nasdaq"
    if "nyse" in n or "new york stock exchange" in n:
        return "nyse"
    return None


def shumway_partial(venue: Optional[str]) -> float:
    return _SHUMWAY.get(venue or "", -0.55)


def last_traded_close(dates: list, closes: list, cutoff: date) -> Optional[float]:
    """Last non-null close at a date <= cutoff ('last traded', spec §16 R-B3). Plan 3's
    coordinator must use THIS definition — position-pairing or a post-cutoff close would
    splice a reused ticker's successor prices in. None on misaligned/empty input.
    Does not assume the series is sorted."""
    if not dates or not closes or len(dates) != len(closes):
        return None
    best_d: Optional[date] = None
    best_c: Optional[float] = None
    for d, c in zip(dates, closes, strict=False):
        if d is None or c is None or d > cutoff:
            continue
        if best_d is None or d > best_d:
            best_d, best_c = d, c
    return best_c


@dataclass(frozen=True)
class FilingRecord:
    """One EDGAR filing, reduced to what classification needs. Built by fetch_filing_records
    (live) or directly in tests/fixtures (offline)."""
    form: str
    filing_date: date
    items: tuple[str, ...] = ()
    filer: Optional[str] = None


@dataclass(frozen=True)
class DelistingVerdict:
    """reason: bankruptcy|mna|unclassified. terminal_return is the return APPENDED to the last
    traded close (Shumway convention): bankruptcy -> -0.30/-0.55 by venue; mna -> 0.0 (last close
    ~= deal value, not a penalty); unclassified -> None (non-measurable, never a guessed sign)."""
    reason: str
    terminal_return: Optional[float]
    delisting_date: date
    venue: Optional[str]
    evidence: tuple[str, ...] = ()


def classify_delisting(records: list, *, window_days: int = 365) -> Optional[DelistingVerdict]:
    """Detect + classify a delisting from the subject CIK's filings. None = not delisted
    (no Form 25/15 family present). Reason from structured 8-K item codes in the
    [delisting_date - window_days, delisting_date] window; Item 1.03 anywhere in the window
    overrides a later 2.01+5.01 (R-B3 — post-Ch.11 asset sales are bankruptcy artifacts).
    Never raises on well-typed FilingRecords."""
    if not records:
        return None
    f25 = [r for r in records if _base_form(r.form).startswith("25")]
    f15 = [r for r in records if _base_form(r.form).startswith("15")]
    anchor_pool = f25 or f15
    if not anchor_pool:
        return None
    delisting_date = min(r.filing_date for r in anchor_pool)
    venue: Optional[str] = None
    for r in sorted(f25, key=lambda r: r.filing_date):
        venue = venue_from_filer(r.filer)
        if venue is not None:
            break

    window_start = delisting_date - timedelta(days=window_days)
    eightks = sorted(
        (r for r in records
         if _base_form(r.form) == "8-K" and window_start <= r.filing_date <= delisting_date),
        key=lambda r: r.filing_date)

    def _ev(r) -> str:
        return f"{_base_form(r.form)} {r.filing_date.isoformat()} items={','.join(r.items)}"

    bankrupt = [r for r in eightks if "1.03" in r.items]
    if bankrupt:                                  # R-B3: 1.03 overrides any later 2.01+5.01
        return DelistingVerdict(BANKRUPTCY, shumway_partial(venue), delisting_date, venue,
                                tuple(_ev(r) for r in bankrupt))
    mna = [r for r in eightks if "2.01" in r.items and "5.01" in r.items]  # SAME filing only
    if mna:
        return DelistingVerdict(MNA, 0.0, delisting_date, venue, (_ev(mna[0]),))
    return DelistingVerdict(UNCLASSIFIED, None, delisting_date, venue, ())


def terminal_price(verdict: Optional[DelistingVerdict], dates: list, closes: list) -> Optional[float]:
    """Route the terminal price by classified reason (§6.6): last traded close <= the delisting
    date, x(1 + terminal_return) — bankruptcy applies the Shumway partial, M&A is unpenalized,
    unclassified -> None (non-measurable). The <= delisting_date bound doubles as the price-side
    ticker-reuse guard (R-A1): never read a close past the delisting."""
    if verdict is None or verdict.terminal_return is None:
        return None
    last = last_traded_close(dates, closes, verdict.delisting_date)
    if last is None:
        return None
    return last * (1.0 + verdict.terminal_return)


_FETCH_FORMS = ("25", "25-NSE", "15-12B", "15-12G", "15-15D", "8-K")


def _record_from_filing(f) -> Optional[FilingRecord]:
    """One edgartools filing -> FilingRecord; None (never raises) on a malformed row."""
    try:
        form = str(getattr(f, "form", "") or "")
        fd = getattr(f, "filing_date", None)
        if isinstance(fd, str):
            fd = date.fromisoformat(fd[:10])
        if not form or not isinstance(fd, date):
            return None
        items = normalize_items(getattr(f, "items", None))
        if not items and _base_form(form) == "8-K":
            try:                                # spike-verified fallback: .obj().items
                items = normalize_items(getattr(f.obj(), "items", None))
            except Exception:  # noqa: BLE001 — a doc-fetch failure must not sink the record
                items = ()
        filer = getattr(f, "filer", None)
        if filer is None and _base_form(form).startswith("25"):
            # Form 25 is filed by the EXCHANGE; the submissions row has no filer name, so read
            # the header (~1-2 Form 25s per delisted name — negligible). Failure -> None ->
            # venue None -> the harsher -0.55 partial (safe degradation; live test pins venue).
            try:
                filer = f.header.filers[0].company_information.name
            except Exception:  # noqa: BLE001
                filer = None
        return FilingRecord(form=form, filing_date=fd,
                            items=items, filer=str(filer) if filer else None)
    except Exception:  # noqa: BLE001
        return None


def fetch_filing_records(cik, identity: str, *, forms=_FETCH_FORMS) -> Optional[list]:
    """Fetch the subject CIK's Form 25/15/8-K filings as FilingRecords. CIK-KEYED — the edgar
    Company lookup is always int-cast from the CIK, never a ticker (the BBBY->Overstock
    ticker-reuse landmine, spec §8). None = fetch failed (warned); [] = fetched fine, nothing
    matched. Items come from the submissions-JSON `items` attribute (free) with a guarded
    .obj() fallback per 8-K."""
    import warnings

    from ..env import redact_secrets
    try:
        cik_int = int(str(cik).lstrip("0") or "0") if str(cik).strip().isdigit() else int(cik)
    except (TypeError, ValueError):
        warnings.warn(f"delisting: malformed CIK {cik!r}", stacklevel=2)
        return None
    try:
        from edgar import Company, set_identity  # edgartools (optional dep, lazy)
        set_identity(identity)
        filings = Company(int(cik_int)).get_filings(form=list(forms))
        seen: set = set()
        out: list[FilingRecord] = []
        for f in filings or []:
            acc = getattr(f, "accession_no", None) or getattr(f, "accession_number", None)
            if acc is not None:
                if acc in seen:                  # edgartools duplicate-row gotcha (edgar_index.py)
                    continue
                seen.add(acc)
            rec = _record_from_filing(f)
            if rec is not None:
                out.append(rec)
        return out
    except Exception as exc:  # noqa: BLE001 — degrade, never crash the backfill
        warnings.warn(f"delisting: filings fetch failed for CIK {cik!r}: "
                      f"{redact_secrets(str(exc))}", stacklevel=2)
        return None
