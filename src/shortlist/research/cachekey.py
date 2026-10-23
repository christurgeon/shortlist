# src/shortlist/research/cachekey.py
"""The on-disk key for one research brief.

The filing accessions alone would go stale only when a new 10-K/10-Q lands, but the
prompt also carries price, valuation, macro regime, insider trades, short interest and
8-K events, and the prompt text itself can change.

THREE PARTS, each guarding a different failure mode:

1. `PROMPT_FINGERPRINT`: a hash of the SOURCE of every module that shapes the prompt or
   the guards, plus the `research` config block. Hashing only `_build_user_prompt` or
   `assess.py` misses callees (`inspect.getsource` does not follow calls) and modules
   such as models.py that contribute `SCHEMA_HINT`. Config is in the hash because
   `research.max_chars`, `research.model` and the `max_*` caps shape the prompt.

2. `context_digest`: a hash of a BUCKETED materiality tuple off the ScoreCard.
   Bucketing stops a raw price from missing the cache on every tick. Everything
   `_quant_context` renders from the card belongs here; the auxiliary lines are hashed
   as their RENDERED strings, so their internals are covered by construction.

3. a day bucket: `research.cache.max_age_days` (0 disables it).

None-safety is required: every StockMetrics field is Optional, and test cards are
duck-typed stubs with no `.metrics`. Every read is getattr-guarded, and every numeric
coercion rejects NaN/inf, because `math.floor(inf)` and `round(nan)` raise and this runs
on the live `/deep` path, where one exception would abort the batch.
"""
from __future__ import annotations

import hashlib
import inspect
import math
from datetime import date, datetime, timezone
from typing import Any, Optional

_FINGERPRINT_FALLBACK = "00000000"

_DEFAULT_MAX_AGE_DAYS = 1
_DEFAULT_PRICE_BAND = 0.03

# Sub-scores print to the prompt as integers, so bucket to 5 points: a 1-point
# wobble cannot change what the model reads.
_SCORE_STEP = 5.0
_CONFIDENCE_STEP = 0.05

# Every module whose source shapes the prompt or the deterministic guards.
# ADD TO THIS LIST when a new context-line renderer gets its own module;
# `test_every_context_line_module_is_hashed` scans for omissions.
# `filings` owns most of the prompt's bytes (`_filing_sections`, `_tenq_mda`,
# `cap_bundle`), and with `textsim` produces `bundle.text_similarity`.
# `earnings_moves` computes values rendered inside the options line. `controls` renders
# the verdict line AND selects the quote that becomes a haystack segment.
# `durability` and `news` render brief sections the model never sees; their wording must
# still not change under a cached brief. Their renderers are not named `context_line`,
# so tests of their own cover them. `capital_allocation` is absent on purpose; see its
# module docstring.
_PROMPT_MODULES = ("assess", "models", "reverse_dcf", "coverage_caveat", "proxy",
                   "gov_contracts", "lobbying", "earnings", "inventory", "riskdiff",
                   "analyst_revision", "options", "earnings_moves", "controls",
                   "filings", "textsim", "eightk", "notes", "durability", "news")

# Excluded from the config hash: output_root is a filesystem path, not prompt
# content; cache's own values already move the key mechanically.
_CONFIG_SKIP = ("output_root", "cache")


def _sha8(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8", "replace")).hexdigest()[:8]


def _module_sources() -> str:
    import importlib
    out = []
    for name in _PROMPT_MODULES:
        mod = importlib.import_module(f".{name}", __package__)
        out.append(inspect.getsource(mod))
    # The numbers of the ROIC-persistence section come from these two root modules and from
    # the committed table. `table_source()` returns "" when the table cannot be read, so a
    # missing table cannot trip the whole-fingerprint fallback below.
    from .. import durability, durability_profile
    out += [inspect.getsource(durability), inspect.getsource(durability_profile),
            durability_profile.table_source()]
    return "\n".join(out)


def _prompt_fingerprint() -> str:
    """8 hex chars over the prompt-shaping module SOURCES. Config is folded in
    per call by brief_key(), since config is a runtime argument. Falls back to a
    constant when source is unavailable (frozen/zipped install): the context and
    day buckets still work, only prompt self-invalidation is lost."""
    try:
        return _sha8(_module_sources())
    except Exception:
        return _FINGERPRINT_FALLBACK


def _config_fingerprint(config: Optional[dict]) -> str:
    """Canonical repr of the `research` config block, minus the keys that do not
    shape the prompt. Sorted so dict insertion order cannot move the key."""
    block = (config or {}).get("research") or {}
    items = sorted((k, repr(v)) for k, v in block.items() if k not in _CONFIG_SKIP)
    return repr(items)


# Source-only fingerprint, computed once. brief_key() folds the config in per
# call, since config is a runtime argument.
PROMPT_FINGERPRINT = _prompt_fingerprint()


def _cache_cfg(config: Optional[dict]) -> dict:
    return ((config or {}).get("research") or {}).get("cache") or {}


def _num(v: Any) -> Optional[float]:
    """float(v) or None. Rejects bools, unparseable values, and NaN/inf — the
    latter two would make math.floor / round raise downstream."""
    if v is None or isinstance(v, bool):
        return None
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return None if math.isnan(x) or math.isinf(x) else x


def _band(v: Any, band: float) -> Optional[int]:
    """Log bucket: values within `band` (a fraction, e.g. 0.03) of each other
    usually share a bucket. Bucket EDGES exist by construction — 100.0 and 101.0
    fall either side of one at band=0.03 (155 vs 156). That is expected: the
    guarantee is "a small move usually does not regenerate", not "never"."""
    x = _num(v)
    if x is None or x <= 0 or band <= 0:
        return None
    return math.floor(math.log(x) / math.log(1.0 + band))


def _step(v: Any, step: float) -> Optional[float]:
    x = _num(v)
    return None if x is None else round(round(x / step) * step, 6)


def _sig3(v: Any) -> Optional[float]:
    """Round to 3 significant figures, so a material move busts the key while
    float noise does not."""
    x = _num(v)
    if x is None:
        return None
    if x == 0:
        return 0.0
    return round(x, 2 - int(math.floor(math.log10(abs(x)))))


def _s(v: Any) -> str:
    """Sort-safe string for a possibly-None field (None and str never compare)."""
    return "" if v is None else str(v)


def _aux_lines(m, config: Optional[dict]) -> list[str]:
    """The auxiliary context lines (gov-contract / lobbying / earnings / inventory /
    analyst-revision), RENDERED, so a field added inside a line is covered automatically.
    All are pure and network-free. Any failure degrades to "" and never raises."""
    rcfg = (config or {}).get("research") or {}
    out = []
    try:
        from . import analyst_revision as analyst_revision_ctx
        from . import earnings as earnings_ctx
        from . import gov_contracts as gov_contracts_ctx
        from . import inventory as inventory_ctx
        from . import lobbying as lobbying_ctx
        for mod, key in ((gov_contracts_ctx, "gov_contracts"),
                         (lobbying_ctx, "lobbying"),
                         (earnings_ctx, "earnings"),
                         (inventory_ctx, "inventory"),
                         (analyst_revision_ctx, "analyst_revision")):
            try:
                out.append(_s(mod.context_line(m, rcfg.get(key))))
            except Exception:
                out.append("")
    except Exception:
        return []
    return out


def _gaps_line(card, config: Optional[dict]) -> str:
    """The rendered DATA GAPS line (`assess._data_gaps_line`, built from
    `card.coverage`/`card.abstentions` via `coverage_caveat.coverage_caveats`), hashed
    as the RENDERED string like `_aux_lines`. `assess` does not import `cachekey`, so the
    import cannot cycle. Mirrors `_build_user_prompt`'s `screening_call.enabled` gate, so
    the digest reacts only to a line the prompt would render. Any failure degrades to ""
    and never raises."""
    if card is None:
        return ""
    scfg = ((config or {}).get("research") or {}).get("screening_call") or {}
    if not scfg.get("enabled", True):
        return ""
    try:
        from .assess import _data_gaps_line
        return _s(_data_gaps_line(card))
    except Exception:
        return ""


def _macro_summary_line(macro, config: Optional[dict]) -> str:
    """The rendered macro-backdrop line (`assess._macro_line`), hashed as the
    RENDERED string, so a same-day move in a printed macro number busts the key even when
    `classify_regime`'s bucket does not change. Degrades to "" (no macro, line disabled,
    or any failure) and never raises."""
    if macro is None:
        return ""
    rcfg = ((config or {}).get("research") or {}).get("macro") or {}
    try:
        from .assess import _macro_line
        return _s(_macro_line(macro, rcfg))
    except Exception:
        return ""


def context_digest(card, macro=None, config: Optional[dict] = None) -> str:
    """8 hex chars over the bucketed materiality tuple. Deliberately EXCLUDES
    DEF 14A proxy facts: they are fetched inside `assess()`, so hashing them would force
    a network call on every cache check. Proxy data moves annually; the day bucket covers
    it. The 8-K substance is excluded for the same reason; its ACCESSIONS ride in via
    `filing_events` and the filing half of the key, and the day bucket covers the residue
    (docs/audits/2026-08-13-eightk-text-in-deep-design.md F1)."""
    raw_band = _num(_cache_cfg(config).get("price_band_pct"))
    band = _DEFAULT_PRICE_BAND if raw_band is None else raw_band
    m = getattr(card, "metrics", None)
    parts: list[Any] = []

    for name in ("quality", "moat", "growth", "momentum", "value", "insider",
                 "risk", "composite"):
        parts.append(_step(getattr(card, name, None), _SCORE_STEP))
    parts.append(_step(getattr(card, "confidence", None), _CONFIDENCE_STEP))
    parts.append(sorted(_s(g) for g in (getattr(card, "gates", None) or [])))
    parts.append(sorted(_s(f) for f in (getattr(card, "flags", None) or [])))
    parts.append(_s(getattr(card, "sic_bucket", None)))

    parts.append(_band(getattr(m, "price", None), band))
    parts.append(_band(getattr(m, "market_cap", None), band))
    parts.append(_step(getattr(m, "short_pct_outstanding", None), 0.001))
    parts.append(_step(getattr(m, "days_to_cover", None), 0.5))
    parts.append(_s(getattr(m, "short_interest_rising", None)))

    # The full Fundamentals + Valuation lines of the quant context.
    for name in ("revenue_cagr", "fcf_cagr", "eps_cagr", "revenue_growth_persistence",
                 "gross_margin", "net_margin", "roic", "debt_to_equity",
                 "interest_coverage", "pe_ttm", "pe_median_5y", "fcf_yield", "peg"):
        parts.append(_sig3(getattr(m, name, None)))

    # `accession` keeps a same-day 8-K/A from colliding with its original.
    events = getattr(m, "filing_events", None) or []
    parts.append(sorted((_s(e.get("form")), _s(e.get("items")), _s(e.get("filed")),
                         _s(e.get("accession")))
                        for e in events if isinstance(e, dict)))

    # Per-trade (date, kind, value bucket), NOT a gross sum: `value` is UNSIGNED
    # (direction lives in `kind`), so a sum loses a buy<->sell flip. Sorted so order
    # cannot move the key.
    trades = getattr(m, "insider_recent", None) or []
    trade_rows = sorted(
        (_s(t.get("date")), _s(t.get("kind")), round((_num(t.get("value")) or 0.0) / 1e5))
        for t in trades if isinstance(t, dict))
    parts.append((len(trades), trade_rows))

    # Every column _render_series prints, not just revenue/FCF.
    series = getattr(m, "financial_series", None) or []
    cols = ("revenue", "gross_profit", "net_income", "operating_cash_flow",
            "free_cash_flow", "cash_and_equivalents", "total_debt", "diluted_eps",
            "diluted_shares")
    parts.append([(_s(r.get("fiscal_year")), _s(r.get("period_end")),
                   *[_sig3(r.get(c)) for c in cols])
                  for r in series if isinstance(r, dict)])

    parts.extend(_aux_lines(m, config))
    parts.append(_gaps_line(card, config))
    parts.append(_macro_summary_line(macro, config))
    return _sha8(repr(parts))


def brief_key(bundle, card, *, macro=None, config: Optional[dict] = None,
              today: Optional[date] = None) -> str:
    """The on-disk key for one brief: filing accessions + prompt fingerprint +
    context digest + day bucket.

    MUST be computed BEFORE the `is_cached` check in `research/__init__.py`. Otherwise
    the pre-LLM skip keys on the narrow accession key and nothing ever regenerates."""
    cfg = _cache_cfg(config)
    raw_age = _num(cfg.get("max_age_days"))
    max_age = _DEFAULT_MAX_AGE_DAYS if raw_age is None else int(raw_age)
    base = (getattr(bundle, "cache_key", "") or
            getattr(bundle, "primary_accession", "") or "unknown")
    prompt8 = _sha8(PROMPT_FINGERPRINT + _config_fingerprint(config))
    key = f"{base}-p{prompt8}-c{context_digest(card, macro, config)}"
    if max_age > 0:
        day = today or datetime.now(timezone.utc).date()
        # A bad max_age or clock must not raise on the live /deep path.
        try:
            bucket = date.fromordinal((day.toordinal() // max_age) * max_age).isoformat()
        except (ValueError, OverflowError):
            bucket = day.isoformat()
        key = f"{key}-{bucket}"
    return key
