from __future__ import annotations

from datetime import date, datetime, timedelta, timezone
from typing import Any, Optional

from ..._util import from_millions as _mm
from ..._util import pct as _pct
from ..models import (
    Analyst,
    Earnings,
    Fundamentals,
    Insider,
    NewsFlow,
    Price,
    Profile,
    SourceResult,
    TickerSnapshot,
)
from .base import _fetch_sections, _KeyedHttpSource

# News-flow windows (fixed priors; only the flag thresholds in config are tunable).
_NEWS_LOOKBACK_DAYS = 30   # company-news fetch window
_NEWS_RECENT_DAYS = 7      # recent vs prior bucket size
# Finnhub's free company-news returns only the ~250 most-recent articles. For a
# high-volume name that cap can fall entirely inside the recent window, so the
# prior bucket is a false 0 -> we DETECT it and mark the window truncated.
_NEWS_TRUNCATE_AT = 240    # near the ~250 cap: a list this long is almost certainly capped


# --- Finnhub: complements with insider sentiment + recommendation trend ----

class FinnhubSource(_KeyedHttpSource):
    name = "finnhub"
    BASE = "https://finnhub.io/api/v1"
    _AUTH_PARAM = "token"
    _ENV_VAR = "FINNHUB_API_KEY"
    _PROVIDER = "finnhub"
    # No retry override: Finnhub's 60/min is comfortable, so the base default
    # (_max_retries = 0, single attempt) is intentional.

    async def fetch(self, ticker: str) -> SourceResult:
        res = SourceResult(source=self.name)
        today = date.today()
        calls = {
            "profile": ("stock/profile2", {"symbol": ticker}),
            "quote": ("quote", {"symbol": ticker}),
            "metric": ("stock/metric", {"symbol": ticker, "metric": "all"}),
            "recommendation": ("stock/recommendation", {"symbol": ticker}),
            "insider_sentiment": ("stock/insider-sentiment", {
                "symbol": ticker,
                "from": (today - timedelta(days=183)).isoformat(), "to": today.isoformat()}),
            "news": ("company-news", {
                "symbol": ticker,
                "from": (today - timedelta(days=_NEWS_LOOKBACK_DAYS)).isoformat(),
                "to": today.isoformat()}),
            "earnings": ("stock/earnings", {"symbol": ticker}),
            # Window reaches BACK ~120d (one quarter + print lag) so a PAST
            # announcement (epsActual) can land in the payload — _earnings' preferred
            # SUE decay anchor. Live-probed 2026-07-09: the FREE tier returns no
            # historical entries at all (even a full past year is empty), so on this
            # plan the anchor comes from the EDGAR 10-Q/10-K filed date (bridge)
            # instead; the reach-back costs nothing and activates on a paid key.
            "earnings_calendar": ("calendar/earnings", {
                "symbol": ticker,
                "from": (today - timedelta(days=120)).isoformat(),
                "to": (today + timedelta(days=90)).isoformat()}),
        }
        await _fetch_sections(res, self._get, calls)
        res.partial = _normalize_finnhub(ticker, res.raw)
        return res


def _news_flow(articles: list, ref: Optional[date] = None) -> NewsFlow:
    """Bucket Finnhub company-news articles into recent/prior/window counts by
    article `datetime` (unix seconds) vs a reference date. Pure."""
    today = ref or date.today()
    recent_cut = today - timedelta(days=_NEWS_RECENT_DAYS)
    prior_cut = today - timedelta(days=2 * _NEWS_RECENT_DAYS)
    recent = prior = window = 0
    latest: Optional[date] = None
    oldest: Optional[date] = None
    for a in articles:
        ts = a.get("datetime")
        if not ts:
            continue
        if ts > 1e12:        # tolerate a millisecond feed (Finnhub sends seconds)
            ts = ts / 1000
        try:
            d = datetime.fromtimestamp(ts, tz=timezone.utc).date()
        except (TypeError, ValueError, OSError):
            continue
        window += 1
        if latest is None or d > latest:
            latest = d
        if oldest is None or d < oldest:
            oldest = d
        if d >= recent_cut:
            recent += 1
        elif d >= prior_cut:
            prior += 1
    # `capped`: the list hit the free-tier ~250 cap (always-noisy name). Separately,
    # `prior_unreliable`: the cap also ate the prior window (no history past 14d), so
    # `prior`'s 0 is a false 0 -> blank it. truncated == capped (honest: any capped list).
    capped = window >= _NEWS_TRUNCATE_AT
    prior_unreliable = capped and oldest is not None and oldest > prior_cut
    return NewsFlow(
        as_of=today.isoformat(), count_recent=recent,
        count_prior=None if prior_unreliable else prior,
        count_window=window, latest_dt=latest.isoformat() if latest else None,
        truncated=capped)


def _earnings(rows: list, calendar: Optional[dict], ref: Optional[date] = None) -> Earnings:
    """Build an Earnings section from Finnhub `stock/earnings` rows (newest-first)
    and a `calendar/earnings` payload. Pure. surprisePercent is already in percent."""
    today = ref or date.today()
    # Sort newest-first by period so correctness doesn't depend on Finnhub's ordering.
    ordered = sorted((rows or []), key=lambda r: r.get("period") or "", reverse=True)
    surprises = [r.get("surprisePercent") for r in ordered
                 if isinstance(r.get("surprisePercent"), (int, float))]
    beats = sum(1 for s in surprises if s > 0) if surprises else None
    # Next report = earliest calendar entry today-or-later with no actual yet (>= so a
    # same-day after-close print isn't dropped on the morning it matters most).
    next_date = None
    cal = (calendar or {}).get("earningsCalendar") or []
    future = sorted(d["date"] for d in cal
                    if d.get("date") and d["date"] >= today.isoformat()
                    and d.get("epsActual") is None)
    if future:
        next_date = future[0]
    # last_report_date: the best-available APPROXIMATION of the most-recent ANNOUNCEMENT
    # date (PREDICTIVE_SIGNALS §1 SUE leg). Finnhub `stock/earnings` rows carry only the
    # fiscal `period` (quarter-END), NOT the print date, so we prefer the `calendar/
    # earnings` entries that DO carry a true announcement `date` AND an `epsActual` (i.e.
    # a report that has already happened, date <= today). Pick the latest such date.
    # Fallback (calendar missing past entries — ALWAYS the case on the free tier,
    # live-probed 2026-07-09): the most recent `stock/earnings` period (quarter-end) — a
    # WEAKER proxy, since the actual print lands ~30-45 days later, so this OVER-states
    # staleness. `last_report_date_estimated` marks the fallback so the bridge can
    # refine the anchor with the EDGAR 10-Q/10-K filed date (~0-5d proxy) without ever
    # degrading a true announcement date. Documented in CLAUDE.md / HARNESS.md.
    last_report_date = None
    estimated = True
    past = sorted(d["date"] for d in cal
                  if d.get("date") and d["date"] <= today.isoformat()
                  and d.get("epsActual") is not None)
    if past:
        last_report_date = past[-1]
        estimated = False
    elif ordered:
        last_report_date = ordered[0].get("period") or None
    return Earnings(
        as_of=today.isoformat(), recent_surprise_pcts=surprises,
        quarters=len(surprises) or None, beats=beats,
        last_surprise_pct=surprises[0] if surprises else None, next_date=next_date,
        last_report_date=last_report_date, last_report_date_estimated=estimated)


def _normalize_finnhub(ticker: str, raw: dict[str, Any]) -> TickerSnapshot:
    snap = TickerSnapshot(ticker=ticker)
    p = raw.get("profile") or {}
    if p:
        snap.profile = Profile(
            name=p.get("name"), industry=p.get("finnhubIndustry"),
            exchange=p.get("exchange"), currency=p.get("currency"),
            country=p.get("country"), market_cap=_mm(p.get("marketCapitalization")),
        )
    m = (raw.get("metric") or {}).get("metric", {})
    if m:
        # `roiTTM` is Return on *Investment*, mapped here as a deliberate ROIC proxy.
        # It only surfaces on the FMP-gated fallback path (FMP leads the fundamentals
        # merge); whether to keep the proxy or drop it to None is an open TECH-DEBT
        # item gated on a quality/moat backtest.
        snap.fundamentals = Fundamentals(
            roe=_pct(m.get("roeTTM")), roic=_pct(m.get("roiTTM")),
            gross_margin=_pct(m.get("grossMarginTTM")),
            net_margin=_pct(m.get("netProfitMarginTTM")),
            debt_to_equity=m.get("totalDebt/totalEquityQuarterly"),
            current_ratio=m.get("currentRatioQuarterly"),
        )
    rows = (raw.get("insider_sentiment") or {}).get("data") or []
    msprs = [r.get("mspr") for r in rows if r.get("mspr") is not None]
    if msprs:
        snap.insider = Insider(sentiment_mspr=max(-1.0, min(1.0, (sum(msprs) / len(msprs)) / 100.0)))
    trend = raw.get("recommendation")
    if isinstance(trend, list) and trend:
        t = trend[0]
        snap.analyst = Analyst(
            buy=(t.get("strongBuy") or 0) + (t.get("buy") or 0),
            hold=t.get("hold"),
            sell=(t.get("sell") or 0) + (t.get("strongSell") or 0),
        )
    q = raw.get("quote") or {}
    if q.get("c"):
        snap.price = Price(price=q["c"])
    news = raw.get("news")
    if isinstance(news, list):          # present (even empty) -> a real 0-count fact
        # Re-bucketed against date.today() every call; correctness on a cache HIT
        # relies on the from/to dates being in the cache key (so it's day-partitioned).
        snap.news = _news_flow(news)
    er = raw.get("earnings")
    if isinstance(er, list):            # present (even empty) -> a real Earnings fact
        snap.earnings = _earnings(er, raw.get("earnings_calendar"))
    return snap
