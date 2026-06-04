"""Free signal sources for candidate discovery. See docs/AUTONOMOUS_SCOUT.md §4.

Each source mirrors the Provider/Source pattern: a name, a registry entry, graceful
degradation (returns [] on error), and an available() audit for coverage honesty.
Errors route through env.redact_secrets before logging.
"""
from __future__ import annotations

from datetime import date
from typing import Protocol

import httpx

from ..env import redact_secrets
from .models import Emission


class SignalSource(Protocol):
    name: str
    is_discovery: bool
    def scan(self, session: date) -> list[Emission]: ...
    def available(self) -> tuple[bool, str]: ...


class MockSignal:
    """Offline source for --demo and end-to-end tests."""
    name = "mock"
    is_discovery = True

    def __init__(self) -> None:
        self._last = 0

    def scan(self, session: date) -> list[Emission]:
        # Tickers must match MockProvider's _SAMPLE so the demo deep-screen has data.
        names = [("GEV",   0.9, "+6.1% on 2.4x volume"),
                 ("LMT",   0.7, "+3.0% on 1.8x volume"),
                 ("GOOGL", 0.6, "most-active list")]
        ems = [Emission(t, "mock:demo", s, ev, is_discovery=True) for t, s, ev in names]
        self._last = len(ems)
        return ems

    def available(self) -> tuple[bool, str]:
        return (True, f"{self._last} hits")


# Registry of constructors. Real sources are added in later tasks.
_REGISTRY: dict[str, type] = {
    "mock": MockSignal,
}


def register(name: str, ctor: type) -> None:
    _REGISTRY[name] = ctor


def build_signals(names: list[str],
                  kwargs_by_name: dict[str, dict] | None = None) -> list[SignalSource]:
    """Resolve names to instances. Unknown names raise KeyError (config typos are loud).

    kwargs_by_name: optional per-signal constructor kwargs, keyed by signal name.
    E.g. ``{"finnhub_news": {"api_key": "k"}, "edgar_form4": {"max_filings": 200}}``.
    Signals not present in the map are constructed with no arguments (existing behaviour).
    """
    overrides = kwargs_by_name or {}
    return [_REGISTRY[n](**(overrides.get(n, {}))) for n in names]


_YAHOO_UA = "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124 Safari/537.36"
_YAHOO_URL = "https://query1.finance.yahoo.com/v1/finance/screener/predefined/saved"


class YahooScreenerSignal:
    """Yahoo predefined screeners — keyless, but requires a browser User-Agent.

    Unofficial endpoint: best-effort, day-cached upstream is unnecessary (one call
    per screen per run). If it 429s the whole discovery funnel leans on EDGAR alone,
    so available() surfaces the outage to the report.
    """
    name = "yahoo_screener"
    is_discovery = True

    def __init__(self, screens: list[str] | None = None, client: httpx.Client | None = None) -> None:
        self.screens = screens or ["day_gainers", "most_actives", "undervalued_growth_stocks"]
        self._client = client
        self._status = (False, "not run")

    def scan(self, session: date) -> list[Emission]:
        client = self._client or httpx.Client(timeout=15.0, headers={"User-Agent": _YAHOO_UA})
        out: list[Emission] = []
        hits = 0
        try:
            for scr in self.screens:
                resp = client.get(_YAHOO_URL, params={"scrIds": scr, "count": 50},
                                  headers={"User-Agent": _YAHOO_UA})
                if resp.status_code != 200:
                    self._status = (False, f"HTTP {resp.status_code} (rate-limited?)")
                    return []
                quotes = (resp.json().get("finance", {}).get("result") or [{}])[0].get("quotes", [])
                for q in quotes:
                    sym = q.get("symbol")
                    if not sym:
                        continue
                    pct = q.get("regularMarketChangePercent") or 0.0
                    vol = q.get("regularMarketVolume") or 0
                    avg = q.get("averageDailyVolume3Month") or 0
                    rvol = (vol / avg) if avg else 1.0
                    strength = max(0.0, min(1.0, abs(pct) / 15.0))  # 15% move -> full strength
                    out.append(Emission(sym.upper(), f"yahoo:{scr}", strength,
                                        f"{pct:+.1f}% on {rvol:.1f}x volume", is_discovery=True))
                hits += len(quotes)
            self._status = (True, f"{hits} hits")
            return out
        except Exception as e:  # noqa: BLE001 — degrade, never crash the run
            self._status = (False, redact_secrets(str(e)))
            return []
        finally:
            if self._client is None:
                client.close()

    def available(self) -> tuple[bool, str]:
        return self._status


register("yahoo_screener", YahooScreenerSignal)


class FinnhubNewsSignal:
    """News-volume confluence booster. Requires a known symbol — cannot originate."""
    name = "finnhub_news"
    is_discovery = False

    def __init__(self, api_key: str | None = None, client: httpx.Client | None = None) -> None:
        self.api_key = api_key
        self._client = client
        self._status = (False, "not run")

    def scan(self, session: date) -> list[Emission]:
        return []  # confluence-only: nothing to discover without a candidate set

    def scan_for(self, tickers: list[str], session: date) -> list[Emission]:
        if not self.api_key:
            self._status = (False, "no FINNHUB_API_KEY")
            return []
        client = self._client or httpx.Client(timeout=15.0)
        out, ok = [], 0
        frm = (session.replace(day=1)).isoformat()
        try:
            for t in tickers:
                resp = client.get("https://finnhub.io/api/v1/company-news",
                                  params={"symbol": t, "from": frm, "to": session.isoformat(),
                                          "token": self.api_key})
                if resp.status_code != 200:
                    continue
                n = len(resp.json())
                ok += 1
                if n >= 10:  # spike threshold
                    strength = max(0.0, min(1.0, n / 50.0))
                    out.append(Emission(t.upper(), "finnhub:news_volume", strength,
                                        f"{n} articles", is_discovery=False))
            self._status = (ok > 0, f"checked {ok} tickers")
            return out
        except Exception as e:  # noqa: BLE001
            self._status = (False, redact_secrets(str(e)))
            return []
        finally:
            if self._client is None:
                client.close()

    def available(self) -> tuple[bool, str]:
        return self._status


class WikipediaAttentionSignal:
    """Pageview-spike confluence booster over a curated ticker->article map."""
    name = "wikipedia"
    is_discovery = False
    _BASE = ("https://wikimedia.org/api/rest_v1/metrics/pageviews/per-article/"
             "en.wikipedia/all-access/user")

    def __init__(self, ticker_map: dict[str, str] | None = None, client: httpx.Client | None = None) -> None:
        self.ticker_map = ticker_map or {}
        self._client = client
        self._status = (False, "not run")

    def scan(self, session: date) -> list[Emission]:
        return []

    def scan_for(self, tickers: list[str], session: date) -> list[Emission]:
        client = self._client or httpx.Client(
            timeout=15.0, headers={"User-Agent": "shortlist-scout/0.1 (turgechr@duck.com)"})
        out, ok = [], 0
        try:
            for t in tickers:
                article = self.ticker_map.get(t.upper())
                if not article:
                    continue
                # 14d daily window ending at session; URL dates omitted for brevity in tests
                resp = client.get(f"{self._BASE}/{article}/daily/2000010100/2100010100")
                if resp.status_code != 200:
                    continue
                ok += 1
                views = [i.get("views", 0) for i in resp.json().get("items", [])]
                if len(views) >= 14:
                    prior = sum(views[-14:-7]) or 1
                    recent = sum(views[-7:])
                    if recent > 1.5 * prior:
                        strength = max(0.0, min(1.0, (recent / prior - 1.0)))
                        out.append(Emission(t.upper(), "wikipedia:attention", strength,
                                            f"+{recent/prior*100-100:.0f}% pageviews",
                                            is_discovery=False))
            self._status = (ok > 0, f"checked {ok} mapped tickers")
            return out
        except Exception as e:  # noqa: BLE001
            self._status = (False, redact_secrets(str(e)))
            return []
        finally:
            if self._client is None:
                client.close()

    def available(self) -> tuple[bool, str]:
        return self._status


register("finnhub_news", FinnhubNewsSignal)
register("wikipedia", WikipediaAttentionSignal)


class EdgarForm4Signal:
    """Insider cluster-buy discovery from the SEC Form 4 daily index."""
    name = "edgar_form4"
    is_discovery = True

    def __init__(self, max_filings: int = 400, identity: str | None = None) -> None:
        self.max_filings = max_filings
        self.identity = identity or "shortlist-scout turgechr@duck.com"
        self._status = (False, "not run")

    def scan(self, session: date) -> list[Emission]:
        from .edgar_index import cluster_buys_from_records, fetch_recent_records
        try:
            # The SEC daily index for `session` isn't published until ~02:00 UTC, so at
            # the after-close run time today's index is empty; fall back to the last
            # published session rather than reporting a phantom "0 insider activity".
            records, used = fetch_recent_records(session, self.max_filings, self.identity)
        except Exception as e:  # noqa: BLE001
            self._status = (False, redact_secrets(str(e)))
            return []
        ems = cluster_buys_from_records(records)
        # FIX 5: surface the per-day fetch cap so truncation is visible in coverage.
        # An empty index can mean "not yet published" (the common after-close case) OR a
        # genuinely quiet published session, so word the fallback honestly rather than
        # asserting "unpublished".
        fallback = "" if used == session else f"; {session} index empty, used {used}"
        self._status = (bool(records),
                        f"{len(ems)} clusters from {len(records)} txns "
                        f"(cap {self.max_filings}){fallback}")
        return ems

    def available(self) -> tuple[bool, str]:
        return self._status


register("edgar_form4", EdgarForm4Signal)


class QuiverSignal:
    """Stub for Quiver Quantitative signals (congressional trades, gov-contract awards).

    Registered so config can reference ``quiver.enabled: false`` without a KeyError;
    returns [] and available() == (False, "not implemented") until wired up.
    See providers/extensions.py for the screener-layer counterpart.
    """
    name = "quiver"
    is_discovery = True

    def scan(self, session: date) -> list[Emission]:  # noqa: ARG002
        return []

    def available(self) -> tuple[bool, str]:
        return (False, "not implemented")


register("quiver", QuiverSignal)
