"""Options-surface context line for the research brief (research-only).

Reframes the options market's forward-looking view of a name — implied volatility
against realized, the move priced into the next earnings print, and 25-delta skew — as
a caveated context line for Claude to reconcile against the filing. NOT a scored or
flagged signal, and NOT on `/screen`. Lives in the prompt, never in the grounding
haystack: these are market prices, so a model that could quote one through
quote-verification would have it "verified" as a filing fact (the reverse_dcf
discipline).

Feed, guards and every constant below: docs/audits/2026-08-24-options-surface-design.md.

A SINGLE FIRM'S NUMBER IS UNINTERPRETABLE, SO EVERY ITEM CARRIES A REFERENCE
---------------------------------------------------------------------------
This is the trap that killed the Lazy-Prices cosine (TODO.md §2a: "a single-firm
absolute cosine is uninterpretable regardless"). A skew of -4.2 vol points and an
implied/realized ratio of 1.75 mean nothing on their own, so the line always prints the
measured large-cap cross-section beside them, with its `n` and its date. Both references
are REGIME-DEPENDENT single-day cross-sections — re-measure with
`docs/audits/scripts/probe_cboe_surface.py --with-realized` and treat one older than
~6 months as indicative only.

DO NOT ADD THE VARIANCE-RISK-PREMIUM CAVEAT BACK
------------------------------------------------
The textbook line — "option prices embed a risk premium, so implied exceeds realized" —
was in an early draft of this module's rendered text. It is MEASURED FALSE on the
committed cross-section: the median IV30/realized is 0.93 and 60 of 80 large caps price
implied UNDER realized (50 of 80 on a 21-day denominator, 71 of 80 on a 63-day, so the
direction is not an artifact of one window). Print the reference, never the prior.

WHY THE REALIZED DENOMINATOR IS THE 252-DAY FIELD AND NOT A 30-DAY ONE
----------------------------------------------------------------------
Matching a 30-day implied vol to a 30-day realized vol looks like the horizon-correct
choice and is not: a trailing 21 trading days can contain an earnings reaction while the
forward 30 days contains none, which is a horizon mismatch wearing the right label. The
252-day window spans four earnings cycles and is correspondingly tighter across the
cross-section (0.73-1.36 against 0.43-1.61). So this module uses the `realized_vol` the
Yahoo source already computes and adds no field.
"""
from __future__ import annotations

import datetime
import os
from dataclasses import dataclass, field
from typing import Any, Optional

# CBOE's delayed-quote chain. Keyless, no signup. One request per deep dive.
CHAIN_URL = "https://cdn.cboe.com/api/global/delayed_quotes/options/{}.json"

# Large-cap 25-delta skew (put IV - call IV, volatility points), n=80, quotes as of
# the 2026-08-21 close. 23 of 80 were negative (calls bid over puts).
SKEW_REFERENCE = {"n": 80, "as_of": "2026-08-21",
                  "p10": -0.9, "median": 0.9, "p90": 3.0}
# Large-cap IV30 / realized-vol(252d), same universe and date. The median below 1.0 is
# the measurement that retired the variance-risk-premium caveat — see the docstring.
IV_RV_REFERENCE = {"n": 80, "as_of": "2026-08-21",
                   "p10": 0.83, "median": 0.93, "p90": 1.07}

# The reference expiry for skew: the listed expiry nearest this many days out. Skew is
# a term-structure-sensitive quantity, so it is only comparable against the reference
# when measured at a comparable tenor.
_SKEW_TARGET_DTE = 30
_DELTA_TARGET = 0.25
_ATM_DELTA = 0.50
# Cap on retained expiries. The chain carries up to 24 for a large cap; the earnings
# selection never looks past a few months, and an unbounded list would put a large
# object on a research object for no gain.
_MAX_EXPIRIES = 16


@dataclass
class OptionsSurface:
    """The reduced options chain for one ticker — a few dozen numbers, never the
    contract array. The raw payload runs to a 674 KB median and 3.0 MB worst case,
    which is why nothing here keeps it."""
    ticker: str
    as_of: str                              # query date, YYYY-MM-DD
    quote_time: str                         # CBOE `last_trade_time`; see staleness below
    spot: Optional[float] = None
    iv30: Optional[float] = None            # percent, e.g. 24.05
    skew_pts: Optional[float] = None        # put IV - call IV at ~25 delta, vol points
    skew_expiry: Optional[str] = None
    skew_dte: Optional[int] = None
    expiries: list = field(default_factory=list)
    # ^ list[dict]: expiry, dte, atm_iv, straddle_pct, atm_spread_pct. `straddle_pct`
    # is None whenever the ATM quote failed the delta or spread guard, so a consumer
    # must treat its absence as "not tradeable", never as zero.

    def stale_days(self, today: Optional[datetime.date] = None) -> Optional[int]:
        """Age of the QUOTES, not of the file. CBOE's file-level `timestamp` moves when
        it regenerates the JSON and is not a freshness signal: on one probe GE's file
        was stamped 2026-08-22 and WFC's 2026-08-24 while both carried 2026-08-21
        quotes. `last_trade_time` is the only honest anchor."""
        if not self.quote_time:
            return None
        try:
            quoted = datetime.date.fromisoformat(self.quote_time[:10])
        except ValueError:
            return None
        return ((today or datetime.date.today()) - quoted).days


def _num(x: Any) -> Optional[float]:
    """A finite number, or None. CBOE sends 0 for an absent quote rather than null."""
    if isinstance(x, bool) or not isinstance(x, (int, float)):
        return None
    return float(x) if x == x and x not in (float("inf"), float("-inf")) else None


def _parse_osi(symbol: str, root: str) -> tuple[datetime.date, str, float]:
    """OSI contract symbol -> (expiry, 'C'|'P', strike).

    `root` is the file's own `data.symbol`, which is not always the ticker requested."""
    body = symbol[len(root):]
    return (datetime.date(2000 + int(body[:2]), int(body[2:4]), int(body[4:6])),
            body[6], int(body[7:]) / 1000.0)


def _tradeable(o: dict) -> bool:
    """A quote worth reading: two-sided AND a positive implied vol.

    AAPL's chain carries a strike at iv 2.0684 (207%) on delta 0.9998 — a stale
    one-sided quote inverting to a meaningless implied vol. One-sided quotes are the
    single largest source of junk in this feed."""
    return bool(_num(o.get("bid")) and _num(o.get("ask")) and _num(o.get("iv")))


def _pick(rows: list[dict], target: float, tolerance: float) -> Optional[dict]:
    """The contract nearest `target` delta, or None when even the nearest misses by
    more than `tolerance`.

    Rejecting on the ACHIEVED delta is the guard that matters, and it is strictly better
    than requiring a minimum contract count: RES produced a 77-volatility-point skew
    from a put at delta -0.888 against a call at 0.869, because only two or three
    contracts per side carried usable quotes and "nearest to 0.25" landed wherever it
    could. A count would have passed that chain; this does not."""
    scored = []
    for o in rows:
        delta = _num(o.get("delta"))
        if delta is None:           # no delta at all: not a candidate, not a near-miss
            continue
        scored.append((abs(abs(delta) - target), o))
    if not scored:
        return None
    miss, best = min(scored, key=lambda t: t[0])
    return best if miss <= tolerance else None


def _mid(o: dict) -> Optional[float]:
    bid, ask = _num(o.get("bid")), _num(o.get("ask"))
    return (bid + ask) / 2 if bid is not None and ask is not None else None


def _spread_pct(o: dict) -> Optional[float]:
    bid, ask = _num(o.get("bid")), _num(o.get("ask"))
    mid = _mid(o)
    return (ask - bid) / mid * 100 if mid else None


def build_surface(payload: dict, today: datetime.date,
                  cfg: Optional[dict]) -> Optional[OptionsSurface]:
    """Reduce a raw CBOE chain to an `OptionsSurface`. Pure; no I/O. Never raises —
    a malformed payload abstains, because this line must never break a brief."""
    try:
        return _build_surface(payload, today, cfg or {})
    except Exception:       # noqa: BLE001 — abstention is the contract
        return None


def _build_surface(payload: dict, today: datetime.date,
                   cfg: dict) -> Optional[OptionsSurface]:
    data = (payload or {}).get("data") or {}
    root = data.get("symbol") or ""
    spot = _num(data.get("current_price"))
    tolerance = float(cfg.get("delta_tolerance", 0.10))
    max_spread = float(cfg.get("max_atm_spread_pct", 40))

    by_expiry: dict[datetime.date, dict[str, list]] = {}
    for o in data.get("options") or []:
        if not isinstance(o, dict) or not _tradeable(o):
            continue
        try:
            expiry, right, _strike = _parse_osi(str(o.get("option") or ""), root)
        except (ValueError, IndexError):
            continue
        # The file RETAINS expired contracts — 21 of 80 large-cap chains carried them,
        # up to 260 on one name. Reading them produced a nonsense 82% implied move
        # before this filter existed.
        if expiry < today:
            continue
        sides = by_expiry.setdefault(expiry, {"C": [], "P": []})
        if right in sides:
            sides[right].append(o)

    surface = OptionsSurface(
        ticker=root, as_of=today.isoformat(),
        quote_time=str(data.get("last_trade_time") or ""),
        spot=spot, iv30=_num(data.get("iv30")))
    if not by_expiry:
        return surface

    for expiry in sorted(by_expiry)[:_MAX_EXPIRIES]:
        sides = by_expiry[expiry]
        call = _pick(sides["C"], _ATM_DELTA, tolerance)
        put = _pick(sides["P"], _ATM_DELTA, tolerance)
        entry: dict[str, Any] = {"expiry": expiry.isoformat(),
                                 "dte": (expiry - today).days,
                                 "atm_iv": None, "straddle_pct": None,
                                 "atm_spread_pct": None}
        if call and put:
            ivs = [_num(call.get("iv")), _num(put.get("iv"))]
            entry["atm_iv"] = round(sum(ivs) / 2, 4)
            spread = _spread_pct(call)
            entry["atm_spread_pct"] = round(spread, 1) if spread is not None else None
            mids = _mid(call), _mid(put)
            # The straddle is the ONE item priced off a premium mid, so it alone takes
            # the spread guard. A mid whose bid-ask is wider than itself is not a price.
            if all(m is not None for m in mids) and spot and \
                    spread is not None and spread <= max_spread:
                entry["straddle_pct"] = round((mids[0] + mids[1]) / spot * 100, 2)
        surface.expiries.append(entry)

    _apply_skew(surface, by_expiry, today, tolerance)
    return surface


def _apply_skew(surface: OptionsSurface, by_expiry: dict,
                today: datetime.date, tolerance: float) -> None:
    """25-delta skew at the expiry nearest ~30 days. Volatilities only, so unlike the
    straddle it takes no spread guard — a wide bid-ask degrades a premium mid far more
    than it degrades an implied vol."""
    expiry = min(by_expiry, key=lambda e: abs((e - today).days - _SKEW_TARGET_DTE))
    sides = by_expiry[expiry]
    put = _pick(sides["P"], _DELTA_TARGET, tolerance)
    call = _pick(sides["C"], _DELTA_TARGET, tolerance)
    if not (put and call):
        return
    put_iv, call_iv = _num(put.get("iv")), _num(call.get("iv"))
    if put_iv is None or call_iv is None:
        return
    surface.skew_pts = round((put_iv - call_iv) * 100, 2)
    surface.skew_expiry = expiry.isoformat()
    surface.skew_dte = (expiry - today).days


def select_earnings_expiry(expiries: list, days_to_earnings: Optional[int],
                           cfg: Optional[dict]) -> Optional[dict]:
    """The expiry whose straddle prices the next earnings print, or None to abstain.

    THE GUARD THIS EXISTS FOR. The vendor earnings calendar revises a still-future date
    routinely: 14 revisions across 42 tickers in ~2 months, median 7 days and max 8
    (CSCO oscillated between 2026-08-11 and 2026-08-19 four times). If the date is
    revised LATER after we pick the first expiry following it, that expiry now falls
    BEFORE the print — so the straddle prices no earnings event at all while the line
    says it prices one. A silent wrong answer is worse than an abstention.

    So the window opens at `days_to_earnings + earnings_date_uncertainty_days` (the
    measured maximum revision) rather than at the predicted date itself, and closes at
    `max_earnings_expiry_gap_days` beyond it, past which the straddle prices the event
    plus weeks of ordinary drift and overstates it."""
    if days_to_earnings is None or not expiries:
        return None
    cfg = cfg or {}
    floor = days_to_earnings + earnings_buffer_days(days_to_earnings, cfg)
    ceiling = days_to_earnings + int(cfg.get("max_earnings_expiry_gap_days", 14))
    usable = [e for e in expiries
              if e.get("straddle_pct") is not None
              and floor <= (e.get("dte") or -1) <= ceiling]
    return min(usable, key=lambda e: e["dte"]) if usable else None


def earnings_buffer_days(days_to_earnings: int, cfg: dict) -> int:
    """How far past the predicted date the expiry must sit — proximity-aware.

    The date FIRMS UP as the print approaches. Across the 14 observed revisions, none
    happened with fewer than 12 days to go (lead times ran 12-36 days). Applying the
    full buffer regardless would skip the weekly expiry that actually straddles a print
    two days away, which is the case the reader cares about most, so inside
    `earnings_date_firm_within_days` (default 7, comfortably below the measured minimum
    lead of 12) the predicted date is taken at face value."""
    firm_within = int(cfg.get("earnings_date_firm_within_days", 7))
    if days_to_earnings < firm_within:
        return 0
    return int(cfg.get("earnings_date_uncertainty_days", 8))


def _reference(ref: dict, fmt: str) -> str:
    return (f"large-cap reference n={ref['n']} as of {ref['as_of']}: "
            f"median {ref['median']:{fmt}}, 10th-90th percentile "
            f"{ref['p10']:{fmt}} to {ref['p90']:{fmt}}")


def _iv_clause(surface: OptionsSurface, m: Any) -> Optional[str]:
    iv30, realized = surface.iv30, getattr(m, "realized_vol", None)
    if iv30 is None or not realized:
        return None
    ratio = (iv30 / 100.0) / realized
    return (f"Implied volatility (30-day) {iv30:.1f}% against {realized * 100:.1f}% "
            f"realized (1-year) — ratio {ratio:.2f}, versus a "
            f"{_reference(IV_RV_REFERENCE, '.2f')}")


def _earnings_clause(surface: OptionsSurface, m: Any, cfg: dict,
                     earnings_moves: Optional[list]) -> Optional[str]:
    days = getattr(m, "earnings_days_to_next", None)
    picked = select_earnings_expiry(surface.expiries, days, cfg)
    if picked is None:
        return None
    uncertainty = int(cfg.get("earnings_date_uncertainty_days", 8))
    gap = picked["dte"] - days
    # The straddle spans the print PLUS `gap` days of ordinary drift, so the gap is
    # rendered rather than hidden: it is the difference between "prices the event" and
    # "prices the event and a fortnight of everything else".
    provenance = (f"vendor-calendar date, historically revised by up to "
                  f"{uncertainty} days" if days >= int(
                      cfg.get("earnings_date_firm_within_days", 7))
                  else "vendor-calendar date, close enough in to be firm")
    clause = (f"Next earnings in ~{days} days ({provenance}): the {picked['expiry']} "
              f"expiry — {gap} day{'s' if gap != 1 else ''} after the print — prices a "
              f"+/-{picked['straddle_pct']:.1f}% move from the at-the-money straddle "
              f"(bid-ask {picked['atm_spread_pct']:.0f}% of mid)")
    if earnings_moves:
        rendered = ", ".join(f"{pct:+.1f}%" for _, pct in earnings_moves)
        clause += (f". The last {len(earnings_moves)} reported quarters actually moved "
                   f"{rendered} close-to-close, announcement dates taken from "
                   f"8-K Item 2.02")
    return clause


def _skew_clause(surface: OptionsSurface) -> Optional[str]:
    if surface.skew_pts is None:
        return None
    direction = ("puts bid over calls" if surface.skew_pts > 0
                 else "calls bid over puts")
    return (f"25-delta skew {surface.skew_pts:+.1f} volatility points ({direction}); "
            f"{_reference(SKEW_REFERENCE, '+.1f')}")


def context_line(surface: Optional[OptionsSurface], m: Any, cfg: Optional[dict],
                 earnings_moves: Optional[list] = None,
                 today: Optional[datetime.date] = None) -> Optional[str]:
    """One self-disclosing brief line, or None to abstain (disabled, no surface, stale
    quotes, or every item failing its own guard).

    Each clause abstains INDEPENDENTLY: a name with tradeable implied vol but an
    untradeable straddle renders the volatility comparison and drops the implied move,
    the same discipline as "a missing sub-score is excluded, never zeroed"."""
    if not cfg or not cfg.get("enabled", False):
        return None
    if surface is None:
        return None
    stale = surface.stale_days(today)
    if stale is None or stale > int(cfg.get("max_stale_days", 5)):
        return None

    clauses = [c for c in (_iv_clause(surface, m),
                           _earnings_clause(surface, m, cfg, earnings_moves),
                           _skew_clause(surface)) if c]
    if not clauses:
        return None

    age = "same day" if stale == 0 else f"{stale} day{'s' if stale != 1 else ''} stale"
    body = ". ".join(clauses)
    # No variance-risk-premium claim here, deliberately — see the module docstring.
    return (f"Options market (CBOE delayed quotes; quotes as of {surface.quote_time[:10]} "
            f"close, {age}). {body}. These are MARKET PRICES, not filing facts and not "
            f"a forecast. Reconcile against the filing: an implied move far out of line "
            f"with what this company's recent prints actually delivered, or a skew "
            f"inverted versus the reference, is a question about what the market expects "
            f"that the MD&A may answer.")


def fetch_surface(ticker: str, cfg: Optional[dict] = None,
                  as_of: Optional[str] = None,
                  today: Optional[datetime.date] = None) -> Optional[OptionsSurface]:
    """The current options surface for `ticker`, or None. Never raises — the line
    simply abstains, but the reason reaches stderr so a systematic outage does not
    look identical to "this name has no options".

    POINT-IN-TIME: CBOE serves only the CURRENT end-of-day surface; there is no history
    endpoint. Fetching it for a past `as_of` would splice today's prices into a
    historical snapshot, so any `as_of` other than today abstains. The line is
    research-only and never scored, so this guards a gap rather than fixing an observed
    leak — but the leak would be silent.

    RATE LIMIT: Cloudflare enforces a rolling per-IP budget on this host. A cookie-less
    loop at 0.25s spacing died at 60 requests with no recovery; a cookie-persisting
    client at 1.0s completed 80. One client, one request per deep dive, and a 429
    abstains rather than retries — this line is optional and must never spend a brief's
    latency on a retry storm.
    """
    from .filings import log_abstain

    today = today or datetime.date.today()
    if as_of and as_of[:10] != today.isoformat():
        return None
    cfg = cfg or {}
    try:
        import httpx
        headers = {"User-Agent": f"shortlist/0.1 (options research; "
                                 f"{os.environ.get('SEC_IDENTITY', 'contact unset')})",
                   "Accept": "application/json"}
        with httpx.Client(timeout=float(cfg.get("timeout", 20)), headers=headers,
                          follow_redirects=True) as client:
            r = client.get(CHAIN_URL.format(ticker.upper()))
            if r.status_code != 200:
                raise RuntimeError(f"HTTP {r.status_code}")
            payload = r.json()
    except Exception as e:      # noqa: BLE001 — never-raises contract
        log_abstain("options surface fetch failed", ticker, e)
        return None
    return build_surface(payload, today, cfg)
