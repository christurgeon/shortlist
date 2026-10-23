from __future__ import annotations

from typing import Optional

from .models import Coverage, ScoreCard

# `risk` is deliberately omitted: it is a composite-only tilt excluded from
# confidence/scored by design, so its absence is never a data gap worth flagging.
_SUBSCORE_FIELDS = ("quality", "moat", "growth", "momentum", "value", "insider")

# Fetch statuses that signal a coverage problem, with their label. Both `_build_note`
# and `coverage_note_line` key off this; "ok" is absent on purpose.
_STATUS_LABEL = {
    "gated_402": "gated (402)",
    "rate_limited_429": "rate-limited (429)",
    "empty": "empty",
    "error": "fetch error",
}

_FMP_NOTE = (
    "FMP gated this symbol (402); analyst-target upside and PEG still need FMP "
    "Starter tier. FCF yield and PE-vs-history are recovered from EDGAR financials "
    "+ Yahoo prices; FMP-sourced ROE/ROIC remain absent."
)

_FMP_RATE_LIMIT_NOTE = (
    "FMP rate-limited this run (429) — the request budget is exhausted, NOT the "
    "symbol gated. Free tier allows ~5 calls/min and 250/day; re-run the affected "
    "tickers in a smaller batch (per-minute throttle) or after the daily reset "
    "(quota exhaustion). FMP Starter tier raises both ceilings."
)


def build_coverage(outcomes: dict[str, str], contributed: set[str], card: ScoreCard,
                   aux_only: frozenset[str] = frozenset(),
                   axisless: frozenset[str] = frozenset()) -> Optional[Coverage]:
    """Assemble a Coverage record, or None when every provider is "ok".

    `outcomes` maps provider/source name -> fetch status: "ok"/"gated_402"/
    "rate_limited_429"/"error" come from the harness adapter in
    `data/coverage_adapt.py`; "empty" is derived HERE (an "ok" provider absent from
    `contributed`). `contributed` is the set of provider names whose own
    fetch returned at least one field — judged BEFORE merge, so a provider that
    fetched real data but lost every field to a higher-priority source still counts
    as contributing. An "ok" provider absent from `contributed` returned nothing
    usable and is reclassified "empty" — except one in `aux_only`, for which having
    nothing to report is normal; it stays "ok". `axisless` names the providers that
    feed no sub-score; the record carries them so a gap is never blamed on one."""
    providers = dict(outcomes)
    for name, status in list(providers.items()):
        if status == "ok" and name not in contributed and name not in aux_only:
            providers[name] = "empty"

    if all(status == "ok" for status in providers.values()):
        return None

    # A sector-masked sub-score is by design, not a gap; excluding it keeps this
    # consistent with the abstentions diagnostic.
    masked = {a["field"] for a in getattr(card, "abstentions", [])
              if a.get("scope") == "subscore" and a.get("reason") == "inapplicable"}
    unavailable = [f for f in _SUBSCORE_FIELDS
                   if getattr(card, f) is None and f not in masked]
    upside = card.metrics.upside_to_target() if card.metrics else None
    if upside is None:
        unavailable.append("upside_to_target")

    return Coverage(providers=providers, unavailable=unavailable,
                    note=_build_note(providers),
                    axisless=tuple(sorted(axisless & providers.keys())))


def _build_note(providers: dict[str, str]) -> Optional[str]:
    flagged = {n: s for n, s in providers.items() if s in _STATUS_LABEL}
    if not flagged:
        return None
    # FMP notes take precedence over the generic one. A 429 is throttling, not gating,
    # so it must not claim the symbol is gated.
    if providers.get("fmp") == "rate_limited_429":
        return _FMP_RATE_LIMIT_NOTE
    # An fmp "error" is transient, so it must not claim "needs Starter tier".
    if providers.get("fmp") in ("gated_402", "empty"):
        return _FMP_NOTE
    return f"{', '.join(sorted(flagged))}: supplied no usable data for this symbol"


def coverage_note_line(ticker: str, cov: Coverage) -> str:
    """One-line stderr rendering, e.g.
    `  SCHW   fmp gated (402) -> value, upside_to_target unavailable`."""
    flagged = [f"{n} {_STATUS_LABEL[s]}"
               for n, s in sorted(cov.providers.items())
               if s in _STATUS_LABEL]
    unavail = ", ".join(cov.unavailable) or "—"
    return f"  {ticker:<6} {'; '.join(flagged)} -> {unavail} unavailable"
