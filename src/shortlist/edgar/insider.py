"""Form 4 insider math: parse, classify (Cohen-Malloy-Pomorski), score strength. No I/O.

The bulk-history side is `dera.py`; both build the SAME `InsiderTxn` from RAW filing fields,
never edgartools' normalized view, which drifts between versions.
"""
from __future__ import annotations

import xml.etree.ElementTree as ET
from dataclasses import dataclass
from datetime import date

from ._ticker_rules import _TRUE, junk_suffix, real_ticker
from .models import Emission


@dataclass(frozen=True)
class InsiderTxn:
    owner_cik: str
    ticker: str
    date: date
    code: str
    shares: float | None
    price: float | None
    plan_10b5_1: bool
    roles: frozenset[str]
    title: str | None = None
    # A joint filing (several <reportingOwner> blocks) has no per-owner attribution, so
    # the owner CIK is a guess. The parser does not guess; qualifies() drops it, so every
    # drop decision lives in one place. Joint filings are common among open-market purchases.
    joint_filing: bool = False
    # The issuer's own CIK, so a discovery Emission can set cik= directly.
    issuer_cik: str = ""

    @property
    def value(self) -> float | None:
        if self.shares is None or self.price is None:
            return None
        return self.shares * self.price


def _flag(raw) -> bool:
    """aff10b5One / isOfficer appear as both 0|1 and false|true in real filings. A
    missing element is False."""
    return str(raw or "").strip().lower() in _TRUE


def _val(node, path: str) -> str | None:
    """Scalar values nest in a <value> child; that child may be absent (a
    <footnoteId>-only price). Returns None rather than fabricating."""
    el = node.find(path)
    if el is None:
        return None
    v = el.find("value")
    text = (v.text if v is not None else el.text) or ""
    text = text.strip()
    return text or None


def _num(node, path: str) -> float | None:
    raw = _val(node, path)
    try:
        return float(raw) if raw is not None else None
    except ValueError:
        return None


def parse_form4_xml(xml: str) -> list[InsiderTxn]:
    """Raw Form 4 XML -> non-derivative transactions. Never raises: malformed input -> []."""
    if not xml:
        return []
    start = xml.find("<ownershipDocument")
    if start >= 0:
        end = xml.find("</ownershipDocument>")
        xml = xml[start:end + len("</ownershipDocument>")] if end > start else xml[start:]
    try:
        root = ET.fromstring(xml)
    except ET.ParseError:
        return []

    ticker = (_val(root, "issuer/issuerTradingSymbol") or "").upper()
    issuer_cik = _val(root, "issuer/issuerCik") or ""
    owner_cik = _val(root, "reportingOwner/reportingOwnerId/rptOwnerCik") or ""
    joint_filing = len(root.findall("reportingOwner")) > 1
    rel = root.find("reportingOwner/reportingOwnerRelationship")
    roles = set()
    title = None
    if rel is not None:
        if _flag(getattr(rel.find("isOfficer"), "text", None)):
            roles.add("officer")
        if _flag(getattr(rel.find("isDirector"), "text", None)):
            roles.add("director")
        if _flag(getattr(rel.find("isTenPercentOwner"), "text", None)):
            roles.add("tenpercent")
        t = rel.find("officerTitle")
        title = (t.text or "").strip() or None if t is not None else None
    # _val, not raw .text: some filers nest it as <aff10b5One><value>1</value>, which raw
    # .text would read as False, silently disabling the 10b5-1 exclusion.
    plan = _flag(_val(root, "aff10b5One"))

    out: list[InsiderTxn] = []
    for tx in root.findall("nonDerivativeTable/nonDerivativeTransaction"):
        raw_date = _val(tx, "transactionDate")
        if not raw_date:
            continue
        try:
            d = date.fromisoformat(raw_date[:10])
        except ValueError:
            continue
        code_el = tx.find("transactionCoding/transactionCode")
        out.append(InsiderTxn(
            owner_cik=owner_cik, ticker=ticker, date=d,
            code=((code_el.text or "").strip() if code_el is not None else ""),
            shares=_num(tx, "transactionAmounts/transactionShares"),
            price=_num(tx, "transactionAmounts/transactionPricePerShare"),
            plan_10b5_1=plan, roles=frozenset(roles), title=title,
            joint_filing=joint_filing, issuer_cik=issuer_cik,
        ))
    return out


ROUTINE = "routine"
OPPORTUNISTIC = "opportunistic"
UNCLASSIFIED = "unclassified"

_LOOKBACK_YEARS = 3


def classify_tier(owner_cik: str, index: dict, as_of: date,
                  lookback_years: int = _LOOKBACK_YEARS) -> str:
    """Cohen-Malloy-Pomorski (JF 2012) routine/opportunistic split.

    ROUTINE       -- traded in the SAME calendar month in each of the last `lookback_years`
                     consecutive years (that consecutive run must fall inside the recent
                     lookback window -- see below). Predictable; ~zero abnormal return.
    OPPORTUNISTIC -- has >= lookback_years distinct trading years and recent activity, but no
                     such consecutive same-month pattern.
    UNCLASSIFIED  -- not enough history, or the history is stale, to judge. Emitted at
                     reduced strength, never dropped.

    Two separate checks, deliberately NOT collapsed into one calendar window:
      1. "Enough history to judge at all" -- >= lookback_years DISTINCT trading years
         anywhere in the record, the most recent no more than `lookback_years` before
         `as_of`. A gap year makes a trader OPPORTUNISTIC, not UNCLASSIFIED.
      2. "Is the pattern routine" -- checked ONLY over the strict last `lookback_years`
         calendar years, so a long-ended same-month streak cannot brand a trader routine
         forever.

    `owner_cik` is zero-padded before the lookup: it is the join key against the DERA
    history index, and a padding mismatch would silently send every insider to UNCLASSIFIED.
    """
    months = index.get((owner_cik or "").strip().zfill(10))
    if not months:
        return UNCLASSIFIED
    distinct_years = {y for (y, _m) in months}
    if len(distinct_years) < lookback_years:
        return UNCLASSIFIED
    if as_of.year - max(distinct_years) > lookback_years:
        return UNCLASSIFIED  # last trade too long ago -- stale, not judgeable
    window_years = [as_of.year - k for k in range(1, lookback_years + 1)]
    for m in range(1, 13):
        if all((y, m) in months for y in window_years):
            return ROUTINE
    return OPPORTUNISTIC


SIGNAL = "edgar:form4_insider_buy"

# Role weights -- UNFITTED PRIORS. CFO-type titles above CEO-type above other
# (Wang-Shin-Francis 2012 find CFO trades more informative than CEO trades).
_TITLE_WEIGHT = ((("chief financial", "cfo"), 1.00),
                 (("chief executive", "ceo", "president"), 0.90),
                 ((), 0.80))


def _title_weight(title: str | None) -> float:
    t = (title or "").lower()
    for needles, w in _TITLE_WEIGHT:
        if not needles or any(n in t for n in needles):
            return w
    return 0.80


def qualifies(txn: InsiderTxn, tier: str, cfg: dict) -> bool:
    if txn.code != "P" or tier == ROUTINE:
        return False
    # Joint filings carry no per-transaction owner attribution, so owner_cik and every
    # tier derived from it would be a guess.
    if txn.joint_filing:
        return False
    if cfg.get("exclude_10b5_1", True) and txn.plan_10b5_1:
        return False
    if not (txn.roles & set(cfg.get("roles") or ("officer", "director"))):
        return False
    v = txn.value
    # PER-TRANSACTION floor, never an aggregate.
    return v is not None and v >= float(cfg.get("min_value", 100_000))


def is_real_ticker(raw: str | None) -> bool:
    return bool(real_ticker(raw))


def emissions_from_txns(txns, index: dict, as_of: date, cfg: dict) -> list[Emission]:
    """Qualifying transactions -> one Emission per ISSUER. Pure."""
    by_ticker: dict[str, list[tuple[InsiderTxn, str]]] = {}
    for t in txns:
        tier = classify_tier(t.owner_cik, index, as_of)
        # A 5th-letter security-type suffix means the symbol is not common stock, so the
        # row is a resolver artifact (see _ticker_rules).
        if not is_real_ticker(t.ticker) or junk_suffix(t.ticker.strip().upper()):
            continue
        if not qualifies(t, tier, cfg):
            continue
        by_ticker.setdefault(t.ticker, []).append((t, tier))

    strengths = cfg.get("tier_strength") or {}
    out: list[Emission] = []
    for ticker, rows in by_ticker.items():
        buyers = {t.owner_cik for t, _ in rows}
        total = sum(t.value or 0.0 for t, _ in rows)
        best_tier = OPPORTUNISTIC if any(x == OPPORTUNISTIC for _, x in rows) else UNCLASSIFIED
        # Default per tier: collapsing OPPORTUNISTIC to 0.6 would erase the distinction the
        # whole classification exists to draw whenever tier_strength is absent.
        tier_mult = float(strengths.get(
            best_tier, 1.0 if best_tier == OPPORTUNISTIC else 0.6))
        role_w = max(_title_weight(t.title) for t, _ in rows)
        size = min(0.30, total / 5_000_000.0)          # materiality, capped
        cluster = min(0.20, 0.10 * (len(buyers) - 1))  # cluster is a BONUS, not a gate
        strength = round(min(1.0, (0.50 + size + cluster) * role_w * tier_mult), 4)
        # One symbol must map to one issuer. Abstain if the rows disagree: Emission.cik is
        # persisted as a permanent record and a wrong CIK is unrecoverable.
        ciks = {t.issuer_cik for t, _ in rows if t.issuer_cik}
        if len(ciks) > 1:
            continue
        issuer_cik = next(iter(ciks), None)
        out.append(Emission(
            ticker, SIGNAL, strength,
            f"{len(buyers)} insider buy(s), ${total/1000:.0f}k ({best_tier})",
            is_discovery=True, cik=issuer_cik,
            meta={"tier": best_tier, "buyers": len(buyers), "value": total},
        ))
    return out
