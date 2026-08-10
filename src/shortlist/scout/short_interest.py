"""FINRA short-interest discovery originator (scout).

A discovery analogue of the harness `crowded_short` flag: surfaces tickers whose short
interest JUMPED vs the prior FINRA settlement cycle, filtered to a moderate (non-extreme)
crowding band. CONTESTED prior (heavy/rising short interest has a negative base rate for a
long book), so it ships disabled at low weight and supplies attention, not direction — the
downstream quality/value/growth scorer + gates decide the sign, the ledger measures it.

Pure aggregator + a sync fetcher that shares the harness FinraSource disk cache. See
docs/superpowers/specs/2026-06-29-finra-short-interest-originator-design.md.
"""
from __future__ import annotations

from pathlib import Path

from ..data import finra
from ..data.diskcache import read_json_cache, write_json_cache
from .models import Emission

_SIGNAL = "finra:short_interest_jump"

# Strength constants (module-level: not config-exposed — only the filter thresholds are).
# Deliberately a low base + jump-driven, capped below the 13D/Form-4 marquee strengths,
# given the contested sign. DTC is a hard band filter, never a strength reward.
_BASE = 0.35
_W_JUMP = 0.35
_JUMP_REF = 4.0     # a +400% jump saturates the jump term (so typical 25-300% jumps spread
                    # across [base, base+w_jump] and top_n is a meaningful cut, not arbitrary)
_CAP = 0.75

# FINRA's zero-volume days-to-cover cap (mirrors bridge._DTC_SENTINEL). The moderate-DTC
# band already excludes it, but drop it explicitly so intent is clear and robust to retuning.
_DTC_SENTINEL = 999.99

# 5th-letter security-type codes on a 5-letter symbol that mean NOT US common stock:
# F=foreign ordinary (the OTC *F junk), Y=ADR, W=warrant, U=unit, R=rights, Q=bankruptcy.
# Only applied to 5-char symbols — 4-char tickers ending in these letters (e.g. WOOF) are fine.
# `X` (open-end mutual fund) was added 2026-08-07: three X-suffixed funds (FTECX, VFLEX,
# BBASX) reached the live picks ledger through `edgar_form4`, and BBASX scored composite
# 100.0 UNGATED — a mutual fund delivered to the analyst as a top-ranked stock idea. Adding
# it is provably neutral for every committed cohort verdict: `_junk_suffix` is reached only
# by `_assemble_8k` and `_assemble_buyback`, whose cohorts hold ZERO X-suffix events across
# 1,843 + 588 rows. (The 13D cohort's two funds — CPRDX, PMFAX — go through `_assemble_13d`,
# which never calls this rule, so they are untouched.) Evidence:
# docs/audits/2026-08-07-funnel-gate-mismatch.md §3.
_FIFTH_LETTER_SUFFIXES = frozenset("FYWURQX")


def _http_fetch_partitions(timeout: float):
    import httpx  # lazy: only needed for live runs
    with httpx.Client(timeout=timeout, headers={"Accept": "application/json"}) as c:
        r = c.get(finra.FINRA_PARTS_URL)
        r.raise_for_status()
        return r.json()


def _http_fetch_pages(settlement: str, timeout: float) -> list:
    """Page the data endpoint for one settlement cycle (the async FinraSource paging,
    sync). Same request shape so the on-disk cache is byte-compatible (shared hits)."""
    import httpx
    rows: list = []
    offset = 0
    with httpx.Client(timeout=timeout, headers={"Accept": "application/json"}) as c:
        while True:
            body = {"limit": finra.FINRA_PAGE, "offset": offset,
                    "compareFilters": [{"fieldName": "settlementDate",
                                        "fieldValue": settlement, "compareType": "EQUAL"}]}
            r = c.post(finra.FINRA_DATA_URL, json=body)
            r.raise_for_status()
            data = r.json()
            page = data if isinstance(data, list) else []
            rows.extend(page)
            if len(page) < finra.FINRA_PAGE:
                break
            offset += finra.FINRA_PAGE
    return rows


def fetch_short_interest_rows(cache_dir: str = ".cache/finra", timeout: float = 30.0, *,
                              _fetch_partitions=None, _fetch_pages=None):
    """Sync FINRA fetch sharing the async FinraSource disk cache.

    Returns ``(rows, settlement)``. Discovers the latest settlement partition, serves it
    from ``.cache/finra/<settlement>.json`` when present, else pages the full dataset and
    writes the **complete, UNFILTERED** row list to that cache — filtering happens later in
    the aggregator, never before the write, so the harness FinraSource still sees every
    symbol. Test seams ``_fetch_partitions`` / ``_fetch_pages`` inject the HTTP."""
    fetch_parts = _fetch_partitions or _http_fetch_partitions
    fetch_pages = _fetch_pages or _http_fetch_pages
    settlement = finra.latest_partition(fetch_parts(timeout))
    if not settlement:
        return [], None
    path = Path(cache_dir) / f"{settlement}.json"
    cached = read_json_cache(path)
    if cached is not None:
        return cached, settlement
    rows = fetch_pages(settlement, timeout)
    write_json_cache(path, rows)
    return rows, settlement


def _is_common_stock(sym: str) -> bool:
    """A plausible US common-stock ticker: 1-5 alphabetic chars, and a 5-letter symbol
    whose 5th letter is NOT a security-type suffix. Drops the warrant / unit / rights /
    foreign-ordinary / ADR / dotted-share-class symbols the FINRA universe also contains
    (the ADV floor + the downstream below_min_mktcap gate are the rest of the skeptic)."""
    if not (sym.isalpha() and 1 <= len(sym) <= 5):
        return False
    if len(sym) == 5 and sym[-1] in _FIFTH_LETTER_SUFFIXES:
        return False
    return True


def short_interest_jumps_from_rows(rows, settlement, *, min_jump_pct=0.25,
                                   min_dtc=3.0, max_dtc=10.0, max_prior_dtc=10.0,
                                   min_avg_daily_volume=100_000.0,
                                   min_prev_short_shares=50_000.0, deny_list=None, top_n=10):
    """Pure aggregation: raw FINRA rows -> one Emission per qualifying ticker.

    Qualifies a row iff: a plausible common-stock symbol; no stock split; a prior short
    position above a real baseline (min_prev_short_shares — so a from-near-zero ramp isn't
    an absurd %) that rose by >= min_jump_pct; adequate average daily volume; the jump
    started off a non-extreme base (prior_dtc <= max_prior_dtc); and current days-to-cover
    is real (not the 999.99 sentinel) and within the moderate band [min_dtc, max_dtc].
    Never raises on a bad row (a raise would zero the whole signal's emissions)."""
    deny = {finra.norm_symbol(d) for d in (deny_list or [])}
    out: list[Emission] = []
    for row in rows:
        sym = (row.get("symbolCode") or "").strip().upper()
        if not _is_common_stock(sym) or finra.norm_symbol(sym) in deny:
            continue
        if finra.flag(row, "stockSplitFlag"):
            continue                                 # split inflates the share-count jump
        cur = finra.num(row, "currentShortPositionQuantity")
        prev = finra.num(row, "previousShortPositionQuantity")
        if (cur is None or prev is None or prev <= 0
                or prev < min_prev_short_shares or cur <= prev):
            continue                                 # need a positive prior baseline and a rise
        jump = (cur - prev) / prev
        if jump < min_jump_pct:
            continue
        adv = finra.num(row, "averageDailyVolumeQuantity")
        if adv is None or adv <= 0 or adv < min_avg_daily_volume:
            continue                                 # liquidity floor / OTC-junk filter
        if prev / adv > max_prior_dtc:
            continue                                 # jump off an already-extreme base
        dtc = finra.num(row, "daysToCoverQuantity")
        if dtc is None or dtc >= _DTC_SENTINEL or not (min_dtc <= dtc <= max_dtc):
            continue                                 # moderate crowding band (ceiling, not floor)
        strength = min(_CAP, _BASE + _W_JUMP * min(1.0, jump / _JUMP_REF))
        ev = (f"short interest +{round(jump * 100)}% "
              f"({prev / 1e6:.1f}M→{cur / 1e6:.1f}M shares), "
              f"{dtc:g} days-to-cover, cycle {settlement}")
        out.append(Emission(sym, _SIGNAL, strength, ev, is_discovery=True))
    # strength desc, then ticker for a deterministic cut when strengths tie (saturated jumps)
    out.sort(key=lambda e: (-e.strength, e.ticker))
    return out[:top_n]
