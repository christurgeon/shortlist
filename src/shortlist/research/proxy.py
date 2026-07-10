"""DEF 14A proxy statement → compensation & governance research context line.

A research-only, prompt-only context line (NEVER the grounding haystack — a
computed/interpretive proxy claim must not pass quote-verification as a filing
fact; the reverse_dcf / gov_contracts discipline). Not scored, not gated, no flag.

The proxy's reliable signal is STRUCTURED XBRL data (Item 402(v) "Pay versus
Performance", mandatory since FY2023), not narrative — there is no clean
related-party / CD&A extractor, so v1 reads the structured fields edgartools'
`ProxyStatement` exposes and renders a curated, evidence-framed line. Fetched in
the research layer per deep-dive (NOT on every screen's snapshot — the heavy
per-ticker fetch is deliberately kept out of the harness).

Design + evidence grading: docs/superpowers/specs/2026-06-27-def14a-proxy-reader-design.md
"""
from __future__ import annotations

import math
import os
import sys
from dataclasses import dataclass, field
from typing import Optional

from ..env import redact_secrets

# edgartools' "<1% / *" beneficial-ownership sentinel (NOT a literal 0.5%).
_SENTINEL_PCT = 0.5


# --------------------------------------------------------------------------- #
# Data
# --------------------------------------------------------------------------- #
@dataclass
class ProxyFacts:
    """Structured fields extracted from one DEF 14A `ProxyStatement`. Every field
    is None-safe; `top_holders` / `pvp` default empty. Comp values are USD floats."""
    ticker: str
    accession: str = ""
    filing_date: str = ""
    has_xbrl: bool = False
    peo_name: Optional[str] = None
    peo_total_comp: Optional[float] = None
    peo_actually_paid_comp: Optional[float] = None
    neo_avg_total_comp: Optional[float] = None
    ceo_pay_ratio: Optional[float] = None              # CEO:median worker ratio (e.g. 533)
    pvp: list[dict] = field(default_factory=list)      # newest-first; fy/peo_ap/neo_ap/tsr/peer_tsr/net_income
    top_holders: list[dict] = field(default_factory=list)  # {name, pct}; 5%+ table, sentinel-cleaned
    insider_trading_policy: Optional[bool] = None
    award_timing_concern: Optional[bool] = None        # True iff MNPI disclosure timed for comp value

    @property
    def cps(self) -> Optional[float]:
        """CEO-to-average-NEO pay multiple = CEO total comp / average-NEO total comp — a
        pay-concentration proxy related to the Bebchuk-Cremers-Peyer CEO pay slice (which
        uses sum-of-top-5; this is the simpler CEO/avg-NEO ratio). None if either is absent/zero."""
        if self.peo_total_comp and self.neo_avg_total_comp and self.neo_avg_total_comp > 0:
            return self.peo_total_comp / self.neo_avg_total_comp
        return None

    def usable(self) -> bool:
        """Worth rendering: XBRL present AND at least one comp/ownership/pvp datum."""
        return bool(self.has_xbrl and (
            self.peo_total_comp is not None or self.peo_actually_paid_comp is not None
            or self.neo_avg_total_comp is not None or self.ceo_pay_ratio is not None
            or self.top_holders or self.pvp))


# --------------------------------------------------------------------------- #
# Small pure coercion helpers
# --------------------------------------------------------------------------- #
def _num(v) -> Optional[float]:
    """Coerce Decimal/int/float/str → finite float, else None (NaN/inf → None)."""
    if v is None:
        return None
    try:
        x = float(v)
    except (TypeError, ValueError):
        return None
    return x if math.isfinite(x) else None


def _str(v) -> Optional[str]:
    """Collapse runs of whitespace (incl. the non-breaking spaces edgartools leaves in
    holder names, e.g. 'The\\xa0Vanguard\\xa0Group') to single spaces; None if empty."""
    if v is None:
        return None
    s = " ".join(str(v).split())
    return s or None


def _opt_bool(v) -> Optional[bool]:
    return v if isinstance(v, bool) else None


def _fy_int(v) -> Optional[int]:
    s = str(v or "")
    return int(s[:4]) if s[:4].isdigit() else None


def _is_real_pct(pct) -> bool:
    """A usable beneficial-ownership percentage (percent units): finite, in (0,100],
    and NOT the edgartools `0.5` "<1% / *" sentinel."""
    x = _num(pct)
    if x is None or x <= 0 or x > 100 or x == _SENTINEL_PCT:
        return False
    return True


def _records(df) -> list[dict]:
    """A pandas DataFrame (or duck-typed stand-in) → list of row dicts; [] on absence."""
    if df is None:
        return []
    to_dict = getattr(df, "to_dict", None)
    if to_dict is None:
        return []
    try:
        recs = to_dict("records")
    except TypeError:
        recs = to_dict(orient="records")
    return list(recs) if recs else []


def _safe(fn, default=None):
    """Call `fn()`, swallowing any exception → `default` (edgartools accessors parse
    HTML/XBRL lazily and can raise; one bad section must not blank the others)."""
    try:
        return fn()
    except Exception:
        return default


# --------------------------------------------------------------------------- #
# Extraction (pure given a ProxyStatement-like object)
# --------------------------------------------------------------------------- #
def _extract_pvp(df) -> list[dict]:
    """Pay-vs-Performance rows, NEWEST-first. Sorted explicitly by fiscal year
    (descending, undated rows last) rather than relying on edgartools' source order —
    a positional reverse would silently flip the alignment verdict if that order changed."""
    out = []
    for r in _records(df):
        out.append({
            "fy": _fy_int(r.get("fiscal_year_end")),
            "peo_ap": _num(r.get("peo_actually_paid_comp")),
            "neo_ap": _num(r.get("neo_avg_actually_paid_comp")),
            "tsr": _num(r.get("total_shareholder_return")),
            "peer_tsr": _num(r.get("peer_group_tsr")),
            "net_income": _num(r.get("net_income")),
        })
    dated = sorted((r for r in out if r["fy"] is not None),
                   key=lambda r: r["fy"], reverse=True)
    return dated + [r for r in out if r["fy"] is None]


def _extract_holders(df) -> list[dict]:
    """5%+ beneficial holders only ({name, pct}), sentinel-cleaned. The
    director_officer rows carry the `0.5` "<1%" sentinel and are dropped wholesale."""
    out = []
    for r in _records(df):
        if str(r.get("holder_type")) != "5pct_holder":
            continue
        pct = _num(r.get("percent_of_class"))
        name = _str(r.get("holder_name"))
        if name and _is_real_pct(pct):
            out.append({"name": name, "pct": pct})
    return out


def _facts_from_proxy(ticker: str, accession: str, filing_date: str, proxy) -> ProxyFacts:
    """Map an edgartools `ProxyStatement` (duck-typed) into ProxyFacts. Each accessor
    is failure-isolated; missing fields stay None."""
    f = ProxyFacts(ticker=ticker, accession=accession, filing_date=filing_date,
                   has_xbrl=bool(_safe(lambda: proxy.has_xbrl, False)))
    f.peo_name = _str(_safe(lambda: proxy.peo_name))
    f.peo_total_comp = _num(_safe(lambda: proxy.peo_total_comp))
    f.peo_actually_paid_comp = _num(_safe(lambda: proxy.peo_actually_paid_comp))
    f.neo_avg_total_comp = _num(_safe(lambda: proxy.neo_avg_total_comp))
    ratio = _safe(lambda: proxy.ceo_pay_ratio)
    f.ceo_pay_ratio = _num(getattr(ratio, "ratio", None)) if ratio is not None else None
    f.pvp = _extract_pvp(_safe(lambda: proxy.pay_vs_performance))
    f.top_holders = _extract_holders(_safe(lambda: proxy.beneficial_ownership))
    f.insider_trading_policy = _opt_bool(_safe(lambda: proxy.insider_trading_policy_adopted))
    f.award_timing_concern = _safe(lambda: proxy.mnpi_disclosure_timed_for_comp_value) is True
    return f


# --------------------------------------------------------------------------- #
# Fetch (edgartools I/O — covered by the live integration test)
# --------------------------------------------------------------------------- #
def _acceptance_date(filing) -> str:
    """Sortable PiT date: the filing's public date as 'YYYY-MM-DD' ("" if unknown)."""
    return str(getattr(filing, "filing_date", "") or "")


def _pick_latest(filings, as_of: Optional[str]):
    """The newest exact-form 'DEF 14A' at-or-before `as_of` (None == now), or None.
    Pure given an iterable of objects with `.form` / `.filing_date` — the look-ahead
    guard (filings dated after as_of are excluded) and exact-form filter live here so
    they are unit-testable without the network."""
    rows = [f for f in filings if str(getattr(f, "form", "")) == "DEF 14A"]
    if as_of is not None:
        rows = [f for f in rows if _acceptance_date(f) and _acceptance_date(f) <= as_of]
    if not rows:
        return None
    rows.sort(key=_acceptance_date, reverse=True)
    return rows[0]


def fetch_proxy(ticker: str, as_of: Optional[str] = None,
                identity: Optional[str] = None) -> Optional[ProxyFacts]:
    """Latest DEF 14A's structured comp/governance facts for `ticker`, or None.

    POINT-IN-TIME: when `as_of` (ISO 'YYYY-MM-DD') is set, only filings whose
    acceptance date is <= as_of are considered (look-ahead guard for any replay;
    None == "now", the live-screen path). Exact-form ("DEF 14A") only — drops
    DEFA14A/amendments. Returns None when there is no usable proxy (no DEF 14A,
    no XBRL, or nothing extractable). Never raises FROM THE edgartools fetch; it does
    raise if SEC_IDENTITY is unset (like its filings.py siblings) — assess() guards that.
    """
    from edgar import Company, set_identity  # lazy: optional [edgar] extra

    ident = identity or os.environ.get("SEC_IDENTITY")
    if not ident:
        raise RuntimeError("SEC_IDENTITY (a contact email) is required by the SEC")
    set_identity(ident)
    try:
        latest = _pick_latest(Company(ticker).get_filings(form="DEF 14A"), as_of)
        if latest is None:
            return None
        facts = _facts_from_proxy(
            ticker, str(getattr(latest, "accession_no", "") or ""),
            _acceptance_date(latest), latest.obj())
        return facts if facts.usable() else None
    except Exception as e:
        # Never-raises contract: the context line simply abstains — but say why
        # on stderr so a systematic failure doesn't hide as "no proxy".
        print(f"research: DEF 14A proxy fetch failed for {ticker}: "
              f"{type(e).__name__}: {redact_secrets(str(e))[:200]}", file=sys.stderr)
        return None


# --------------------------------------------------------------------------- #
# Render (pure; curated + evidence-framed)
# --------------------------------------------------------------------------- #
def _money(x: float) -> str:
    """Signed USD magnitude. The sign matters: Item 402(v) 'compensation actually paid'
    can be negative (underwater equity in a down year) — abs()'ing it would flip the
    pay-for-performance read the caller relies on."""
    sign = "-" if x < 0 else ""
    a = abs(x)
    if a >= 1e6:
        return f"{sign}${a / 1e6:.1f}M"
    if a >= 1e3:
        return f"{sign}${a / 1e3:.0f}K"
    return f"{sign}${a:.0f}"


def _fy_suffix(facts: ProxyFacts) -> str:
    if facts.pvp and facts.pvp[0].get("fy"):
        return f", FY{facts.pvp[0]['fy']}"
    yr = (facts.filing_date or "")[:4]
    return f", FY{yr}" if yr.isdigit() else ""


def _pvp_clause(pvp: list[dict]) -> Optional[str]:
    """Pay-for-performance alignment over the available window. The decision-useful
    red is CEO actually-paid comp RISING while TSR FELL. Needs >=2 rows with both."""
    rows = [r for r in pvp if r.get("peo_ap") is not None and r.get("tsr") is not None]
    if len(rows) < 2:
        return None
    d_comp = rows[0]["peo_ap"] - rows[-1]["peo_ap"]      # rows[0] newest
    d_tsr = rows[0]["tsr"] - rows[-1]["tsr"]
    if d_comp > 0 and d_tsr < 0:
        return "pay-for-performance misaligned (CEO actually-paid comp rose as TSR fell)"
    if (d_comp > 0) == (d_tsr > 0):                       # moved the same direction
        return "pay-for-performance aligned (CEO actually-paid comp tracks TSR)"
    return None


def _ownership_clause(holders: list[dict], cfg: dict) -> Optional[str]:
    rows = [h for h in holders if h.get("name") and h.get("pct") is not None]
    if not rows:
        return None
    rows.sort(key=lambda h: h["pct"], reverse=True)   # largest-first (the documented order)
    n = int(cfg.get("max_holders", 3))
    named = ", ".join(f"{h['name']} {h['pct']:.1f}%" for h in rows[:n])
    clause = f"top holders {named}"
    top = rows[0]
    if top["pct"] >= float(cfg.get("control_pct", 30.0)):
        clause += (f"; concentrated control (largest {top['name']} {top['pct']:.1f}%) "
                   "— skin-in-the-game vs entrenchment, double-edged")
    return clause


def _gov_clause(facts: ProxyFacts) -> Optional[str]:
    notes = []
    if facts.insider_trading_policy is False:
        notes.append("no insider-trading policy disclosed")
    elif facts.insider_trading_policy is True:
        notes.append("insider-trading policy in place")
    if facts.award_timing_concern:
        notes.append("award-timing/MNPI concern flagged")
    return "governance: " + ", ".join(notes) if notes else None


def context_line(facts: Optional[ProxyFacts], cfg: Optional[dict]) -> Optional[str]:
    """One curated, evidence-framed proxy line for the prompt, or None to abstain
    (disabled / no facts / nothing usable). Prompt-only — never the haystack."""
    if not cfg or not cfg.get("enabled"):
        return None
    if facts is None or not facts.usable():
        return None

    parts: list[str] = []
    if facts.peo_total_comp is not None:
        who = f"CEO {facts.peo_name}" if facts.peo_name else "CEO"
        s = f"{who} comp {_money(facts.peo_total_comp)}"
        if facts.peo_actually_paid_comp is not None:
            s += f" (actually-paid {_money(facts.peo_actually_paid_comp)})"
        parts.append(s)
    if facts.cps is not None:
        parts.append(f"CEO pay {facts.cps:.1f}x avg NEO")
    if facts.ceo_pay_ratio is not None:
        parts.append(f"pay ratio {facts.ceo_pay_ratio:.0f}x median (context)")
    pvp = _pvp_clause(facts.pvp)
    if pvp:
        parts.append(pvp)
    own = _ownership_clause(facts.top_holders, cfg)
    if own:
        parts.append(own)
    gov = _gov_clause(facts)
    if gov:
        parts.append(gov)
    if not parts:
        return None

    caveat = ("Context only — not 10-K text; associated with governance/valuation, "
              "not a return prediction; reconcile against the business.")
    return f"Proxy (DEF 14A{_fy_suffix(facts)}): " + "; ".join(parts) + ". " + caveat
