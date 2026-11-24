"""DEF 14A proxy statement → compensation & governance research context line.

A research-only, prompt-only context line (NEVER the grounding haystack: an
interpretive claim must not pass quote-verification as a filing fact). Not scored,
not gated, no flag.

The reliable signal is STRUCTURED XBRL data (Item 402(v) "Pay versus Performance"),
not narrative, so this reads the structured fields edgartools' `ProxyStatement` exposes.
Fetched per deep dive, not on every screen: the heavy fetch stays out of the harness.
"""
from __future__ import annotations

import copy
import math
import re
from dataclasses import dataclass, field
from typing import Optional

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
        """CEO total comp / average-NEO total comp, a pay-concentration proxy. None if
        either is absent or zero."""
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


_BLOCK_TAGS = frozenset({"div", "p", "br", "li", "tr"})
# Source-HTML newlines are ordinary whitespace, so blocks are split on a private-use
# character instead (lxml rejects control characters in element text).
_BLOCK_SEP = ""
_TRAILING_FOOTNOTE = re.compile(r"(?:\s*(?:\((?:\d{1,2}|[a-z])\)|\*+))+$")
_NON_ALNUM = re.compile(r"\W+")
_NAME_CONTINUES = re.compile(r"(?:,|&|\band|\bof|\bthe|\bits)$", re.I)
_CORP_SUFFIX_LINE = re.compile(
    r"^\D*\b(?:Inc\.?|LLC|L\.?L\.?C\.?|LLP|L\.?L\.?P\.?|L\.?P\.?|Ltd\.?|Co\.?|Company"
    r"|Corp\.?|Corporation|Group|Trust|N\.A\.|plc|AG|S\.A\.)$", re.I)
# A street number, a P.O. box, or a "City, ST 12345" line starts the address.
_ADDRESS_LINE = re.compile(r"^\d|^P\.?\s*O\.?\s*Box\b|,\s*[A-Z]{2}\s+\d{5}", re.I)


def _cell_lines(cell) -> list[str]:
    """A table cell's text split at block boundaries (div/p/br/...), whitespace-collapsed,
    empty lines dropped. lxml's `text_content()` joins those blocks with no separator."""
    cell = copy.deepcopy(cell)
    for el in cell.iter():
        if el is not cell and el.tag in _BLOCK_TAGS:
            el.text = _BLOCK_SEP + (el.text or "")
            el.tail = _BLOCK_SEP + (el.tail or "")
    return [s for s in (_str(x) for x in cell.text_content().split(_BLOCK_SEP)) if s]


def _name_from_lines(lines: list[str]) -> str:
    """The leading lines that form the name: a name wrapped across blocks continues while
    a line ends in a connector ("and", ",") or the next line is a bare corporate suffix
    ("Insurance Company"); the first other line starts the address."""
    parts = [_TRAILING_FOOTNOTE.sub("", lines[0]).strip()]
    for nxt in lines[1:]:
        nxt = _TRAILING_FOOTNOTE.sub("", nxt).strip()
        if _ADDRESS_LINE.search(nxt) or not (
                _NAME_CONTINUES.search(parts[-1]) or _CORP_SUFFIX_LINE.match(nxt)):
            break
        parts.append(nxt)
    return " ".join(p for p in parts if p).rstrip(",").strip()


def _ownership_name_lines(tree) -> list[list[str]]:
    """Line-split text of every multi-line cell in the table edgartools reads beneficial
    ownership from. Uses edgartools' private table finder so both see the same table."""
    from edgar.proxy.html_extractor import (
        _OWNERSHIP_HEADING_ANCHORS,
        _OWNERSHIP_HEADING_REJECTS,
        _find_section_table,
        _score_ownership_table,
    )

    table = _find_section_table(tree, _OWNERSHIP_HEADING_ANCHORS,
                                _OWNERSHIP_HEADING_REJECTS, _score_ownership_table)
    if table is None:
        return []
    cells = (c for c in table.iter("td", "th"))
    return [lines for lines in map(_cell_lines, cells) if len(lines) > 1]


def _holder_name(name: str, cell_lines: list[list[str]]) -> str:
    """The holder's own name, taken from the multi-line cell whose glued text `name` is
    (longest first line wins); `name` unchanged when no cell matches. Matching needs the
    cell's full text to contain `name`, so a line from another cell cannot truncate it."""
    key = _NON_ALNUM.sub("", name)
    best = None
    for lines in cell_lines:
        first = lines[0]
        if not name.startswith(first):
            continue
        if len(key) <= len(_NON_ALNUM.sub("", first)):
            continue
        if not _NON_ALNUM.sub("", "".join(lines)).startswith(key):
            continue
        if best is None or len(first) > len(best[0]):
            best = lines
    if best is None:
        return name
    return _name_from_lines(best) or name


def _extract_holders(df, cell_lines: Optional[list[list[str]]] = None) -> list[dict]:
    """5%+ beneficial holders only ({name, pct}), sentinel-cleaned. The
    director_officer rows carry the `0.5` "<1%" sentinel and are dropped wholesale.
    `cell_lines` (from `_ownership_name_lines`) strips addresses glued onto names."""
    out = []
    for r in _records(df):
        if str(r.get("holder_type")) != "5pct_holder":
            continue
        pct = _num(r.get("percent_of_class"))
        name = _str(r.get("holder_name"))
        if name and _is_real_pct(pct):
            out.append({"name": _holder_name(name, cell_lines or []), "pct": pct})
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
    f.top_holders = _extract_holders(_safe(lambda: proxy.beneficial_ownership),
                                     _safe(lambda: _ownership_name_lines(proxy._html_tree), []))
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
    from edgar import Company  # lazy: optional [edgar] extra

    from .filings import log_abstain, require_identity

    require_identity(identity)
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
        log_abstain("DEF 14A proxy fetch failed", ticker, e)
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
