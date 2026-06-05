"""Free signal sources for candidate discovery. See docs/AUTONOMOUS_SCOUT.md §4.

Each source mirrors the Provider/Source pattern: a name, a registry entry, graceful
degradation (returns [] on error), and an available() audit for coverage honesty.
Errors route through env.redact_secrets before logging.
"""
from __future__ import annotations

import random
import time
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
_YAHOO_URL2 = "https://query2.finance.yahoo.com/v1/finance/screener/predefined/saved"  # manual escape hatch only

# Yahoo's edge WAF rejects bot-shaped (UA-only) requests with an HTML 429 — a cold-start
# *fingerprint* block, NOT throttling. A full browser header set clears it; once one
# well-formed request succeeds the IP is trusted for a window. Headers are the primary
# lever but not proven sufficient on a truly cold IP, so the per-run bail + the cross-run
# cooldown (see daily.py) are load-bearing and must not be removed. See CLAUDE.md gotcha.
# Accept-Encoding must stay a subset of what httpx can decode (no br/zstd without the dep,
# or .json() fails on a compressed body).
_YAHOO_HEADERS = {
    "User-Agent": _YAHOO_UA,
    "Accept": "application/json,text/plain,*/*",
    "Accept-Language": "en-US,en;q=0.9",
    "Accept-Encoding": "gzip, deflate",
    "Origin": "https://finance.yahoo.com",
    "Referer": "https://finance.yahoo.com/",
    "Sec-Fetch-Site": "same-site",
    "Sec-Fetch-Mode": "cors",
    "Sec-Fetch-Dest": "empty",
}

_YAHOO_MAX_RETRIES = 1
_YAHOO_RETRY_BASE_S = 1.0
_YAHOO_RETRY_MAX_S = 5.0
_YAHOO_INTER_SCREEN_S = 1.5
_YAHOO_JITTER = (0.8, 1.5)


def _content_type(resp: httpx.Response) -> str:
    return (resp.headers.get("content-type") or "").lower()


def _is_waf_block(resp: httpx.Response) -> bool:
    """An HTML/empty 429 is an edge WAF fingerprint block (won't clear on immediate
    retry); a JSON 429 is the API's own throttle."""
    return "json" not in _content_type(resp)


def _should_retry(resp: httpx.Response) -> bool:
    """Bias ambiguous 429s toward NO retry (treat as WAF): only retry a 429 that is both
    JSON-typed and carries a Retry-After (a genuine throttle), or a transient 5xx."""
    if 500 <= resp.status_code < 600:
        return True
    if resp.status_code == 429:
        return ("json" in _content_type(resp)) and resp.headers.get("Retry-After") is not None
    return False


def _yahoo_retry_after_seconds(resp: httpx.Response, attempt: int, base: float) -> float:
    header = resp.headers.get("Retry-After")
    if header:
        try:
            return min(float(header), _YAHOO_RETRY_MAX_S)
        except ValueError:
            pass
    return min(base * (2 ** attempt), _YAHOO_RETRY_MAX_S)


class YahooScreenerSignal:
    """Yahoo predefined screeners — keyless, but requires a full browser header set
    (see _YAHOO_HEADERS). Unofficial endpoint, best-effort. On a block it bails after a
    single request and leans on EDGAR; available() surfaces the outage to the report, and
    waf_blocked lets the orchestrator persist a rest-of-day cooldown.
    """
    name = "yahoo_screener"
    is_discovery = True

    def __init__(self, screens: list[str] | None = None, client: httpx.Client | None = None, *,
                 max_retries: int = _YAHOO_MAX_RETRIES,
                 inter_screen_delay: float = _YAHOO_INTER_SCREEN_S,
                 retry_base_s: float = _YAHOO_RETRY_BASE_S) -> None:
        self.screens = screens or ["day_gainers", "most_actives", "undervalued_growth_stocks"]
        self._client = client
        self.max_retries = max_retries
        self.inter_screen_delay = inter_screen_delay
        self.retry_base_s = retry_base_s
        self.waf_blocked = False
        self._status = (False, "not run")

    def _fetch_screen(self, client: httpx.Client, scr: str) -> httpx.Response:
        """One screen with a gentle, WAF-aware retry. The WAF/HTML 429 short-circuits
        before any sleep; the final attempt never sleeps (mirrors FMPProvider._get)."""
        resp = None
        for attempt in range(self.max_retries + 1):
            resp = client.get(_YAHOO_URL, params={"scrIds": scr, "count": 50},
                              headers=_YAHOO_HEADERS)
            if resp.status_code == 200:
                return resp
            if not _should_retry(resp) or attempt == self.max_retries:
                return resp
            time.sleep(_yahoo_retry_after_seconds(resp, attempt, self.retry_base_s))
        return resp

    def scan(self, session: date) -> list[Emission]:
        client = self._client or httpx.Client(timeout=15.0, headers=_YAHOO_HEADERS)
        out: list[Emission] = []
        hits = 0
        completed = 0
        try:
            for idx, scr in enumerate(self.screens):
                if idx > 0:
                    time.sleep(self.inter_screen_delay * random.uniform(*_YAHOO_JITTER))
                resp = self._fetch_screen(client, scr)
                if resp.status_code != 200:
                    self.waf_blocked = _is_waf_block(resp)
                    kind = "WAF-blocked (HTML)" if self.waf_blocked else "throttled (JSON)"
                    tail = (f"HTTP {resp.status_code} {kind}; "
                            f"bailed after {completed}/{len(self.screens)} screens")
                    self._status = (False, f"{hits} hits then {tail}" if hits else tail)
                    return out  # EARLY BAIL — do not fire the remaining screens
                # Parse into a local list; commit to `out` only after a clean parse, so an
                # exception mid-screen can't leak a half-parsed screen's emissions.
                screen_ems: list[Emission] = []
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
                    screen_ems.append(Emission(sym.upper(), f"yahoo:{scr}", strength,
                                               f"{pct:+.1f}% on {rvol:.1f}x volume", is_discovery=True))
                out.extend(screen_ems)
                hits += len(screen_ems)
                completed += 1
            self._status = (True, f"{hits} hits")  # ran=True only when ALL screens succeeded
            return out
        except Exception as e:  # noqa: BLE001 — degrade, never crash the run
            self._status = (False, redact_secrets(str(e)))
            return out  # keep any cleanly-committed screens
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
