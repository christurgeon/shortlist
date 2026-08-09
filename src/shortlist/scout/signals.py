"""Free signal sources for candidate discovery. See docs/AUTONOMOUS_SCOUT.md §4.

Each source mirrors the Provider/Source pattern: a name, a registry entry, graceful
degradation (returns [] on error), and an available() audit for coverage honesty.
Errors route through env.redact_secrets before logging.
"""
from __future__ import annotations

import random
import time
from datetime import date, timedelta
from typing import Protocol

import httpx

from ..env import redact_secrets
from .models import Emission
from .sec_throttle import sec_throttle


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
        # Tickers must match the harness mock SAMPLE (data/mockdata.py) so the demo deep-screen has data.
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

# Shared across every CIK->ticker-resolver-backed originator (13D, stake-increase, 8-K,
# buyback): an empty resolver would abstain on EVERY row (no display_names fallback), so
# each `scan()` bails LOUDLY with this exact message rather than reading as a quiet day.
_CIK_RESOLVER_EMPTY_MSG = "CIK→ticker resolver empty; skipped — retry next session"

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
        before any sleep; the final attempt never sleeps (mirrors FMPSource._get)."""
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
        frm = (session - timedelta(days=7)).isoformat()  # fixed trailing 7-day window
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
                # Request ~last 30 days (ample headroom for the trailing-14-day slice
                # below: views[-14:-7] / views[-7:]).
                start = (session - timedelta(days=30)).strftime("%Y%m%d00")
                end = session.strftime("%Y%m%d00")
                resp = client.get(f"{self._BASE}/{article}/daily/{start}/{end}")
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
    """Opportunistic-insider buy discovery from SEC Form 4 (docs/FORM4_INSIDER.md).

    Drift capture, NOT an information edge: Form 4 is public T+2 and every vendor parses it
    instantly. The edge, if any, is selectivity over a months-long post-filing drift, via the
    Cohen-Malloy-Pomorski (JF 2012) routine/opportunistic split (scout/insider.py,
    scout/dera.py) rather than the retired same-session same-issuer cluster heuristic.

    `fetch_submissions`/`load_index` are injection points for tests (no network): the
    default `fetch_submissions` fetches the live SEC Form 4 daily index
    (edgar_index.fetch_form4_submissions), and the default `load_index` builds/loads the
    DERA classification history (dera.load_index) from `cfg["dera"]`
    (`quarters`/`cache_dir`, defaulting to 16 quarters / `.cache/dera`).

    Config-absence contract (C-2, 2026-07-27): `cfg=None` (no `scout.form4` block) makes
    `scan()` INERT -- no fetch, no DERA index build/download (~205 MB). This is distinct
    from `cfg={}` (a present-but-empty block), which still runs on the code-level defaults
    baked into `insider.qualifies`/`emissions_from_txns`. The two used to collapse to the
    same thing (`cfg or {}`), which made "block removed" untestable against a signal that
    still silently ran -- see docs/FORM4_INSIDER.md §8.
    """
    name = "edgar_form4"
    is_discovery = True

    def __init__(self, cfg: dict | None = None, max_filings: int = 2000,
                 identity: str | None = None,
                 fetch_submissions=None, load_index=None) -> None:
        self._config_present = cfg is not None
        self.cfg = cfg or {}
        self.max_filings = max_filings
        self.identity = identity or "shortlist-scout turgechr@duck.com"
        self._fetch = fetch_submissions
        self._load_index = load_index
        self._index_missing: int | None = None
        self._status = (False, "not run")

    def _default_fetch(self, session: date, cap: int):
        from .edgar_index import fetch_form4_submissions
        return fetch_form4_submissions(session, cap, self.identity)

    def _default_index(self, session: date) -> dict:
        from .dera import load_index, quarters_back
        dcfg = self.cfg.get("dera") or {}
        quarters = quarters_back(session, int(dcfg.get("quarters", 16)))
        index, n_missing = load_index(dcfg.get("cache_dir", ".cache/dera"), quarters,
                                      self.identity)
        self._index_missing = n_missing   # C-1: surfaced in available() below
        return index

    def scan(self, session: date) -> list[Emission]:
        if not self._config_present:
            self._status = (False, "no scout.form4 config")
            return []

        from .insider import emissions_from_txns, parse_form4_xml
        fetch = self._fetch or self._default_fetch
        load_idx = self._load_index or (lambda: self._default_index(session))
        try:
            docs, used, considered = fetch(session, self.max_filings)
            index = load_idx()
        except Exception as e:  # noqa: BLE001
            self._status = (False, redact_secrets(str(e)))
            return []

        # I-1: fetch_form4_submissions' hard-failure sentinel is `used is None` (a real SEC
        # outage / edgartools error, caught internally rather than raised) -- distinct from
        # a normal walk-back exhaustion (`used == session`, "tried every lookback day,
        # nothing published"). Without this a multi-day SEC outage and a quiet day both
        # read as "0 insider buys from 0 Form 4s", indistinguishable.
        sec_failed = used is None

        txns = [t for doc in docs for t in parse_form4_xml(doc)]
        # Spec §5.1: joint filings are abstained, and the count must be VISIBLE -- a silent
        # 9.5% drop is exactly the kind of thing that hides a broken parser.
        n_joint = len({t.ticker for t in txns if t.joint_filing and t.code == "P"})
        ems = emissions_from_txns(txns, index, session, self.cfg)

        # I-2: name the overflow, don't just drop it -- spec §5.1's "never dropped silently"
        # rule and the buyback signal's overflow-naming precedent both apply here too.
        cap = int(self.cfg.get("daily_cap", 25))
        overflow: list[str] = []
        if len(ems) > cap:
            ems.sort(key=lambda e: e.strength, reverse=True)
            overflow = [e.ticker for e in ems[cap:]]
            ems = ems[:cap]

        # An empty index can mean "not yet published" (the common after-close case) OR a
        # genuinely quiet published session, so word the fallback honestly rather than
        # asserting "unpublished". Suppressed when sec_failed -- `fail` below already covers it.
        fallback = ("" if sec_failed or used == session
                    else f"; {session} index empty, used {used}")
        joint = f"; {n_joint} joint filings abstained" if n_joint else ""
        over = (f"; {len(overflow)} emission(s) over daily_cap: {', '.join(overflow)}"
                if overflow else "")
        fail = "; SEC fetch failed (see logs)" if sec_failed else ""
        # C-1: the DERA index's own size/completeness, so a permanently-empty or
        # persistently-thin history (e.g. every download failing) is visible here too, not
        # just silently downgrading everyone to UNCLASSIFIED at reduced strength.
        idx_note = f"; DERA index {len(index)} owner(s)"
        if self._index_missing:
            idx_note += f" ({self._index_missing} quarter(s) missing)"
        # Fetch volume genuinely reaching the daily cap means the day's filings were
        # truncated in EDGAR index order -- an uncharacterizable sampling bias, not a
        # complete scan. That must be visibly distinct from a quiet day that merely
        # happens to mention the same cap number in its status string (silent truncation
        # is exactly the coverage defect edgar_index_daily_cap: 2500 exists to minimize).
        # I-6: key the marker off filings CONSIDERED, not returned. fetch_form4_submissions
        # drops filings that error, so len(docs) < max_filings even when the cap bound --
        # and a >cap day almost certainly contains at least one error, so the marker used to
        # vanish on exactly the days it exists for. Silent index-order truncation is the
        # defect the cap raise was meant to remove.
        truncated = " TRUNCATED" if considered >= self.max_filings else ""
        # I-4: an empty index is not a quiet day -- every insider then classifies
        # UNCLASSIFIED, nothing is dropped as routine (measured: 48.5% of the population),
        # and the signal emits roughly double the names at 0.6 strength. That state is
        # unambiguously broken, so it must not read as `ran`.
        index_dead = self._index_missing is not None and not index
        self._status = (bool(docs) and not sec_failed and not index_dead,
                        f"{len(ems)} insider buys from {len(docs)} Form 4s "
                        f"(cap {self.max_filings}{truncated}){joint}{over}{fail}"
                        f"{idx_note}{fallback}")
        return ems

    def available(self) -> tuple[bool, str]:
        return self._status


register("edgar_form4", EdgarForm4Signal)


class EdgarActivist13DSignal:
    """Initial SCHEDULE 13D activist stakes from the SEC daily index (discovery).

    A fresh 13D = an investor crossed 5% with intent to influence — a leading catalyst
    for a re-rating, skewed toward smaller/interesting US names. Keyless and VPS-safe
    (pure SEC EDGAR; no Yahoo WAF). We screen after-close, so these are activist
    re-rating candidates to WATCH / pass to /deep (the post-filing drift), not early-pop
    trades. The raw firehose is noise-dominated, so quality.py drops SPAC/affiliate noise
    and a curated marquee list boosts credible activists; the scorer stays the skeptic.
    """
    name = "edgar_activist_13d"
    is_discovery = True

    def __init__(self, identity: str | None = None, max_filings: int = 300,
                 drop_spacs: bool = True, drop_affiliates: bool = True,
                 marquee_boost: float = 0.2, cache_dir: str = ".cache/sec_tickers",
                 stake_fetcher=None) -> None:
        self.identity = identity or "shortlist-scout turgechr@duck.com"
        self.max_filings = max_filings
        self.drop_spacs = drop_spacs
        self.drop_affiliates = drop_affiliates
        self.marquee_boost = marquee_boost
        self.cache_dir = cache_dir
        self.stake_fetcher = stake_fetcher
        self._resolver: dict[str, str] | None = None
        self._status = (False, "not run")

    def scan(self, session: date) -> list[Emission]:
        from .cik_tickers import load_cik_to_ticker, resolve_ticker
        from .edgar_index import activist_stakes_from_records, fetch_recent_activist_records
        try:
            if self._resolver is None:
                self._resolver = load_cik_to_ticker(self.identity, cache_dir=self.cache_dir)
            if self._resolver == {}:
                # An empty CIK->ticker resolver would abstain on EVERY subject (no
                # display_names fallback), so a broken resolver reads as a quiet day and
                # filings age out of the walk-back window unemitted. Mirror the buyback
                # degraded guard: skip LOUDLY, drop the memoized resolver so "retry next
                # session" is real.
                self._resolver = None
                self._status = (False, _CIK_RESOLVER_EMPTY_MSG)
                return []
            resolver = self._resolver

            def resolve(cik):
                return resolve_ticker(cik, resolver)

            records, used = fetch_recent_activist_records(
                session, self.max_filings, self.identity, resolve)
        except Exception as e:  # noqa: BLE001 — degrade, never crash the run
            self._status = (False, redact_secrets(str(e)))
            return []

        stakes: dict[str, float] = {}
        if self.stake_fetcher is not None:
            for r in records:
                pct = self.stake_fetcher(r)
                if pct is not None and r.get("accession"):
                    stakes[r["accession"]] = pct

        ems = activist_stakes_from_records(
            records, drop_spacs=self.drop_spacs, drop_affiliates=self.drop_affiliates,
            marquee_boost=self.marquee_boost, stake_by_accession=stakes or None)
        fallback = "" if used == session else f"; {session} index empty, used {used}"
        self._status = (True, f"{len(ems)} activist 13D from {len(records)} filings"
                        f" (cap {self.max_filings}){fallback}")
        return ems

    def available(self) -> tuple[bool, str]:
        return self._status


register("edgar_activist_13d", EdgarActivist13DSignal)


def _stake_from_filing(filing) -> float | None:
    from .stake import stake_pct_from_filing
    return stake_pct_from_filing(filing)


def _prior_stake(subject_cik: str | int | None, filer_cik10: str | None, before: date,
                 identity: str) -> float | None:
    from .stake import fetch_prior_stake
    return fetch_prior_stake(subject_cik, filer_cik10, before, identity)


class EdgarStakeIncreaseSignal:
    """Material stake-INCREASE 13D/A amendments (discovery; measure-first, ships OFF).

    The escalation analogue of EdgarActivist13DSignal: an activist who crossed 5% and
    keeps buying is a conviction signal the initial filing can't carry. Keyless +
    VPS-safe (pure SEC EDGAR). Ships disabled at weight 0.5 pending the pre-registered
    backfill verdict (preregister/edgar_13d_stake_increase.yaml) — the buyback/8-K
    measure-first precedent. Emission rule + abstentions live in the pure
    edgar_index.stake_increases_from_records; this class does fetch budgeting only.

    Population-scope caveat: this live walker drops an amendment row whose subject
    ticker fails to resolve at scan time (edgar_index.fetch_amendment_records),
    BEFORE any baseline is seeded, while the 13d-a backfill keeps every row and
    resolves the ticker point-in-time only at emission — so the measured backfill
    cohort's population is slightly broader than what live scanning ever emits.
    """
    name = "edgar_13d_stake_increase"
    is_discovery = True

    def __init__(self, identity: str | None = None, max_filings: int = 300,
                 min_increase_pp: float | None = None, max_prior_fetches: int = 10,
                 drop_spacs: bool = True, drop_affiliates: bool = True,
                 seen_accessions: list[str] | None = None,
                 baselines: dict | None = None,
                 cache_dir: str = ".cache/sec_tickers") -> None:
        self.identity = identity or "shortlist-scout turgechr@duck.com"
        self.max_filings = max_filings
        self.min_increase_pp = min_increase_pp
        self.max_prior_fetches = max_prior_fetches
        self.drop_spacs = drop_spacs
        self.drop_affiliates = drop_affiliates
        self.seen = set(seen_accessions or [])
        self.baselines = dict(baselines or {})
        self.cache_dir = cache_dir
        self._resolver: dict[str, str] | None = None
        self.new_accessions: list[str] = []
        self.baseline_updates: dict[str, dict] = {}
        self._status = (False, "not run")

    def _resolver_map(self) -> dict[str, str]:
        from .cik_tickers import load_cik_to_ticker
        if self._resolver is None:
            self._resolver = load_cik_to_ticker(self.identity, cache_dir=self.cache_dir)
        return self._resolver

    def scan(self, session: date) -> list[Emission]:
        from . import edgar_index
        from .cik_tickers import resolve_ticker
        from .stake import pair_key
        try:
            resolver = self._resolver_map()
            if resolver == {}:
                self._resolver = None
                self._status = (False, _CIK_RESOLVER_EMPTY_MSG)
                return []

            def resolve(cik):
                return resolve_ticker(cik, resolver)

            records, used = edgar_index.fetch_recent_amendment_records(
                session, self.max_filings, self.identity, resolve)
        except Exception as e:  # noqa: BLE001 — degrade, never crash the run
            self._status = (False, redact_secrets(str(e)))
            return []

        records = [r for r in records if r.get("accession") not in self.seen]
        # Cheap index-level quality filters BEFORE the doc-fetch + prior-fetch budget spend
        # (the design mandate: amendments only fetch after passing these). The aggregator
        # below applies the same predicates again (pure-function contract, belt-and-braces).
        from .quality import is_affiliate_filing, is_spac_or_shell
        records = [r for r in records
                   if not (self.drop_spacs and is_spac_or_shell(r.get("subject_name") or ""))
                   and not (self.drop_affiliates
                            and is_affiliate_filing(r.get("activist") or "",
                                                    r.get("subject_name") or ""))]
        # Doc-fetch stake % per surviving record; cold-start prior fetches are budgeted.
        prior_budget, overflow = self.max_prior_fetches, 0
        attempted: set[str] = set()   # a pair spends prior-fetch budget at most once/scan
        for r in records:
            r["stake_pct"] = _stake_from_filing(r.get("_filing"))
            pk = pair_key(r.get("filer_cik"), r.get("cik"))
            if (pk and pk not in self.baselines and r["stake_pct"] is not None):
                if pk in attempted:
                    continue           # already attempted this scan; skip (no re-spend, no re-count)
                attempted.add(pk)
                if prior_budget > 0:
                    prior_budget -= 1
                    try:
                        fd = date.fromisoformat(str(r.get("file_date"))[:10])
                    except ValueError:
                        fd = session
                    prior = _prior_stake(r.get("cik"), r.get("filer_cik"), fd,
                                         self.identity)
                    if prior is not None:
                        self.baselines[pk] = {"pct": prior, "date": ""}
                else:
                    overflow += 1
        ems, updates = edgar_index.stake_increases_from_records(
            records, self.baselines, min_increase_pp=self.min_increase_pp,
            drop_spacs=self.drop_spacs, drop_affiliates=self.drop_affiliates)
        self.baseline_updates = updates
        self.new_accessions = [r["accession"] for r in records if r.get("accession")]
        fallback = "" if used == session else f"; {session} index empty, used {used}"
        over = (f"; {overflow} pair(s) skipped prior-fetch budget"
                if overflow else "")
        self._status = (True, f"{len(ems)} stake increases from {len(records)} 13D/A "
                        f"(cap {self.max_filings}){over}{fallback}")
        return ems

    def available(self) -> tuple[bool, str]:
        return self._status


register("edgar_13d_stake_increase", EdgarStakeIncreaseSignal)


class FinraShortInterestSignal:
    """FINRA short-interest jump discovery (the crowded_short flag's discovery analogue).

    Surfaces tickers whose short interest jumped vs the prior FINRA settlement cycle,
    filtered to a moderate (non-extreme) crowding band. A CONTESTED prior — heavy/rising
    short interest has a NEGATIVE base rate for a long book — so it ships disabled at low
    weight and supplies attention, not direction: the downstream scorer + gates judge the
    sign, the selection ledger measures it. Keyless + VPS-safe (no Yahoo WAF). Emits once
    per NEW settlement cycle (the data updates only ~bi-monthly), gated on `last_settlement`
    from ScoutState. See scout/short_interest.py and the design spec.
    """
    name = "finra_short_interest"
    is_discovery = True

    def __init__(self, *, cache_dir: str = ".cache/finra", timeout: float = 30.0,
                 last_settlement: str | None = None, min_jump_pct: float = 0.25,
                 min_dtc: float = 3.0, max_dtc: float = 10.0, max_prior_dtc: float = 10.0,
                 min_avg_daily_volume: float = 100_000.0,
                 min_prev_short_shares: float = 50_000.0,
                 deny_list: list[str] | None = None, top_n: int = 10) -> None:
        self.cache_dir = cache_dir
        self.timeout = timeout
        self.last_settlement = last_settlement
        self._params = {"min_jump_pct": min_jump_pct, "min_dtc": min_dtc, "max_dtc": max_dtc,
                            "max_prior_dtc": max_prior_dtc,
                            "min_avg_daily_volume": min_avg_daily_volume,
                            "min_prev_short_shares": min_prev_short_shares,
                            "deny_list": list(deny_list or []), "top_n": top_n}
        self.settlement: str | None = None   # discovered cycle (daily.py persists it)
        self._status = (False, "not run")

    def scan(self, session: date) -> list[Emission]:
        from .short_interest import fetch_short_interest_rows, short_interest_jumps_from_rows
        try:
            rows, settlement = fetch_short_interest_rows(self.cache_dir, self.timeout)
        except Exception as e:  # noqa: BLE001 — degrade, never crash the run
            self._status = (False, redact_secrets(str(e)))
            return []
        self.settlement = settlement
        if settlement is None:
            self._status = (False, "no FINRA partition found")
            return []
        if settlement == self.last_settlement:
            self._status = (True, f"cycle {settlement} already processed")
            return []
        ems = short_interest_jumps_from_rows(rows, settlement, **self._params)
        self._status = (True, f"{len(ems)} short-interest jumps from {len(rows)} symbols"
                              f" (cycle {settlement})")
        return ems

    def available(self) -> tuple[bool, str]:
        return self._status


register("finra_short_interest", FinraShortInterestSignal)


class EdgarEightKSignal:
    """8-K positive-pocket discovery from EDGAR full-text search (EFTS; data/efts.py).

    Surfaces filings whose items contain a configured AND-set (default 1.01∧3.03 — the
    only positive-drift pocket in Lerman-Livnat 2010). A CONTESTED prior, the FINRA
    pattern: the unconditional 8-K sign is NEGATIVE (Zhao 2017) and filing-day moves
    reverse, so it ships DISABLED at weight 0.5 and supplies attention, not direction —
    the scorer + gates judge the sign, the pre-registered backfill cohort (edgar_8k.yaml)
    earns or kills it. Keyless + VPS-safe (SEC-hosted, no Yahoo WAF). EFTS lags a day or
    two, so scan() walks session-2..session and dedups via a capped accession-seen set in
    ScoutState (daily.py persists `new_accessions` after each scan).
    """
    name = "edgar_8k"
    is_discovery = True

    def __init__(self, identity: str | None = None, *, item_sets=None, deny_list=None,
                 drop_spacs: bool = True, daily_cap: int = 6, lookback_days: int = 2,
                 seen_accessions: list[str] | None = None,
                 cache_dir: str = ".cache/efts",
                 resolver_cache_dir: str = ".cache/sec_tickers") -> None:
        self.identity = identity or "shortlist-scout turgechr@duck.com"
        self.item_sets = [list(s) for s in (item_sets or [["1.01", "3.03"]])]
        self.deny_list = list(deny_list or [])
        self.drop_spacs = drop_spacs
        self.daily_cap = daily_cap
        self.lookback_days = lookback_days
        self.seen_accessions = set(seen_accessions or [])
        self.cache_dir = cache_dir
        self.resolver_cache_dir = resolver_cache_dir
        self._resolver: dict[str, str] | None = None
        self.new_accessions: list[str] = []   # daily.py persists these into ScoutState
        self._status = (False, "not run")

    def scan(self, session: date) -> list[Emission]:
        from ..data import efts
        from .cik_tickers import load_cik_to_ticker, resolve_ticker
        from .eightk import eightk_events_from_rows
        try:
            if self._resolver is None:
                self._resolver = load_cik_to_ticker(self.identity,
                                                    cache_dir=self.resolver_cache_dir)
            if self._resolver == {}:
                # An empty CIK->ticker resolver would abstain on EVERY row (no display_names
                # fallback), yet a naive run would still record new_accessions and permanently
                # suppress those filings. Mirror the buyback degraded guard: skip, record
                # nothing, drop the memoized resolver so "retry next session" is real.
                self._resolver = None
                self.new_accessions = []
                self._status = (False, _CIK_RESOLVER_EMPTY_MSG)
                return []
            resolver = self._resolver
            rows: list[dict] = []
            failed: list[str] = []
            # EFTS lags (today often total: 0) — walk back session-2..session; the
            # accession-seen state below stops the overlap re-emitting across runs.
            for back in range(self.lookback_days, -1, -1):
                day = session - timedelta(days=back)
                got = efts.fetch_eightk_day(day, identity=self.identity,
                                            cache_dir=self.cache_dir)
                if got is None:
                    failed.append(day.isoformat())
                else:
                    rows.extend(got)
        except Exception as e:  # noqa: BLE001 — degrade, never crash the run
            self._status = (False, redact_secrets(str(e)))
            return []
        ems = eightk_events_from_rows(
            rows, resolve_ticker_fn=lambda cik: resolve_ticker(cik, resolver),
            item_sets=self.item_sets, deny_list=self.deny_list,
            drop_spacs=self.drop_spacs)
        fresh = [e for e in ems if e.meta.get("adsh") not in self.seen_accessions]
        fresh = fresh[:self.daily_cap]
        self.new_accessions = [e.meta["adsh"] for e in fresh]
        tail = f"; failed days: {', '.join(failed)}" if failed else ""
        self._status = (not failed,
                        f"{len(fresh)} 8-K matches from {len(rows)} filings "
                        f"(cap {self.daily_cap}){tail}")
        return fresh

    def available(self) -> tuple[bool, str]:
        return self._status


register("edgar_8k", EdgarEightKSignal)


class EdgarThirteenFSignal:
    """Marquee-fund new-position cloning from SEC 13F information tables (discovery).

    Per curated fund CIK, once per session: pick the latest EXACT 13F-HR (amendments
    excluded), diff its holdings against the immediately-prior 13F-HR, and surface each NEW
    position (CUSIP present now, absent before) that clears a within-book weight floor. An
    established-positive academic prior (Martin-Puthenpurackal 2008; Cohen-Polk-Silli 2010
    "best ideas"), so it ships ENABLED — but at weight 1.0, below the 13D/Form-4 tier because
    the information is up to 45 days stale (the clone return is measured from the FILING date,
    the lag priced into the literature). Keyless + VPS-safe (pure SEC, no Yahoo WAF).

    A SECOND diff (`scout.thirteenf.material_add`, ON) runs over the same two books at zero
    extra SEC cost and emits `edgar:13f_material_add` for positions the fund materially ADDED
    to — detected on SHARE count, because `value` is quarter-end market value and a price rise
    alone would read as conviction. It carries its own weight (0.75) via `cfg_key_for`, its own
    per-filing `top_n`, and `meta["kind"]` so the two cohorts stay separable. Disabled or
    absent => byte-identical to new-positions-only. Design:
    `docs/audits/2026-08-09-13f-material-adds-design.md`.

    Seen-accession semantics differ from the 8-K originator deliberately: a fund's latest
    13F-HR is marked processed ONCE the diff runs, even when it yields zero new positions —
    otherwise an empty-diff fund re-downloads both infotables every day forever. `max_filings
    _per_day` caps processing (13F volume is quarterly-bursty); unprocessed filings stay
    UNSEEN and are picked up on later sessions (carry-over, never dropped). daily.py persists
    `processed_accessions` (NOT emissions) into ScoutState.thirteenf_seen_accessions.
    """
    name = "edgar_13f"
    is_discovery = True

    def __init__(self, identity: str | None = None, *, funds=None,
                 min_position_pct: float = 0.005, full_strength_pct: float = 0.05,
                 max_filings_per_day: int = 3, top_n: int = 10,
                 deny_list: list[str] | None = None,
                 material_add: dict | None = None,
                 seen_accessions: list[str] | None = None, timeout: float = 30.0,
                 resolver_cache_dir: str = ".cache/sec_tickers",
                 ftd_cache_dir: str = ".cache/sec_ftd") -> None:
        self.identity = identity or "shortlist-scout turgechr@duck.com"
        self.funds = [dict(f) for f in (funds or [])]
        self.min_position_pct = min_position_pct
        self.full_strength_pct = full_strength_pct
        self.max_filings_per_day = max_filings_per_day
        self.top_n = top_n
        # Second diff over the SAME two books (zero extra SEC requests). Absent or disabled
        # => byte-identical to the new-positions-only behaviour.
        # Coerced defensively: a present-but-empty YAML key (`ratio:` -> None) would otherwise
        # raise TypeError in __init__ and kill the ENTIRE 13F signal, new positions included,
        # rather than degrading to the default.
        ma = dict(material_add or {})
        self.material_add_on = bool(ma.get("enabled"))
        self.material_add_ratio = float(ma.get("ratio") or 1.50)
        self.material_add_top_n = int(ma.get("top_n") or 5)
        self.deny_list = list(deny_list or [])
        self.seen_accessions = set(seen_accessions or [])
        self.timeout = timeout
        self.resolver_cache_dir = resolver_cache_dir
        self.ftd_cache_dir = ftd_cache_dir
        self._resolver = None
        # The PROCESS-WIDE budget, not a private one: a per-signal throttle cannot bound the
        # process's SEC request rate (audit §4).
        self._throttle = sec_throttle()
        self.processed_accessions: list[str] = []   # daily.py persists these into ScoutState
        self._status = (False, "not run")

    def scan(self, session: date) -> list[Emission]:
        from . import thirteenf as tf
        from .cusip_map import load_cusip_resolver
        try:
            if self._resolver is None:
                self._resolver = load_cusip_resolver(
                    self.identity, resolver_cache_dir=self.resolver_cache_dir,
                    ftd_cache_dir=self.ftd_cache_dir, timeout=self.timeout,
                    today=session, throttle=self._throttle)
        except Exception as e:  # noqa: BLE001 — resolver build never crashes the scan
            self._status = (False, redact_secrets(str(e)))
            return []

        # An empty FTD CUSIP index (the likely datacenter-IP-block / FTD-outage case) would
        # abstain on nearly EVERY CUSIP — the exact-match name fallback alone is far too weak
        # to justify burning quarters (_mark_processed permanently suppresses a quarter's
        # candidates). So abort on an empty FTD index REGARDLESS of the name index: skip the
        # whole scan, mark nothing, and drop the memoized resolver so "retry next session" is
        # real even for a long-lived instance. (Fakes injected in tests lack cusip_to_symbol ->
        # getattr -> None, and `None == {}` is False, so the real-resolver path alone is gated.)
        cusip_idx = getattr(self._resolver, "cusip_to_symbol", None)
        if cusip_idx == {}:
            self._resolver = None       # force a rebuild next session (real retry)
            self._status = (False, "CUSIP resolver FTD index empty (likely FTD outage / "
                                   "datacenter-IP block); skipped — retry next session")
            return []

        out: list[Emission] = []
        processed = 0
        abstained = 0
        n_new = 0            # NOT len(out): `out` also carries material adds
        n_adds = 0
        add_abstained = 0
        errors = 0
        last_error: str | None = None
        capped = False
        skipped: list[str] = []
        for fund in self.funds:
            if processed >= self.max_filings_per_day:
                capped = True
                break                                 # carry-over: leave the rest unseen
            cik = fund.get("cik")
            fund_name = fund.get("name") or str(cik)
            if cik is None:
                continue
            try:
                subm = tf.fetch_submissions(cik, self.identity, timeout=self.timeout,
                                            throttle=self._throttle)
                filings = tf.parse_submissions_13fhr(subm)
                if not filings:
                    continue
                latest = filings[0]
                if latest["accession"] in self.seen_accessions:
                    continue                           # already processed a prior session
                if len(filings) < 2:
                    # No prior 13F-HR to diff against — mark processed so we don't refetch
                    # daily, emit nothing (first-ever filer; never for the marquee seed).
                    self._mark_processed(latest["accession"])
                    processed += 1
                    continue
                # An empty parsed row list is indistinguishable from a swallowed fetch/parse
                # failure (unrecognized infotable member, ET.ParseError -> []), and a real
                # 13F-HR ALWAYS carries an infotable. EDGAR is uncached, so a transient
                # truncation heals on a second fetch -> retry ONCE in-scan before giving up
                # (see _fetch_infotable_retry). Trade-off: still-empty after the retry MARKS
                # the accession processed and skips the quarter LOUDLY (a truly transient
                # empty then loses one quarter — accepted) rather than the old never-mark
                # policy, which wedged a fund into re-fetching daily for ~2 quarters with the
                # signal status pinned to failed.
                latest_rows = self._fetch_infotable_retry(tf, cik, latest["accession"])
                if not latest_rows:
                    errors += 1
                    skipped.append(f"{fund_name} {latest['accession']}: empty infotable parse "
                                   "— quarter skipped")
                    self._mark_processed(latest["accession"])
                    processed += 1
                    continue
                prior = filings[1]
                prior_rows = self._fetch_infotable_retry(tf, cik, prior["accession"])
                if not prior_rows:
                    # If prior parses empty we'd emit the whole current book as "new" — skip
                    # the quarter, but mark the LATEST processed so we don't refetch daily.
                    errors += 1
                    skipped.append(f"{fund_name} {prior['accession']}: empty infotable parse "
                                   "— quarter skipped")
                    self._mark_processed(latest["accession"])
                    processed += 1
                    continue
                latest_agg = tf.aggregate_positions(latest_rows)
                prior_agg = tf.aggregate_positions(prior_rows)
                new_pos = tf.new_position_diff(
                    latest_agg, prior_agg,
                    min_position_pct=self.min_position_pct,
                    full_strength_pct=self.full_strength_pct)
                ems, abst = tf.thirteenf_emissions(
                    new_pos, resolve_fn=self._resolver.resolve, fund_name=fund_name,
                    period=latest["period"], filing_date=latest["filing_date"],
                    fund_cik=cik, accession=latest["accession"],
                    deny_list=self.deny_list, top_n=self.top_n)
                out.extend(ems)
                n_new += len(ems)
                abstained += abst
                if self.material_add_on:
                    # Same two books, no refetch. Emitted AFTER new positions because
                    # `picks.pick_from_card` falls back to `emissions[0]` for a pick's
                    # `catalyst` (picks.py), so on a ticker BOTH kinds hit, the new position
                    # — the sharper event — is the one recorded in the ledger. The two
                    # per-filing budgets are independent (`top_n` vs `material_add_top_n`),
                    # so ordering does NOT arbitrate a shared cap.
                    adds, add_abst = tf.material_add_diff(
                        latest_agg, prior_agg,
                        min_position_pct=self.min_position_pct,
                        full_strength_pct=self.full_strength_pct,
                        material_add_ratio=self.material_add_ratio)
                    add_ems, add_res_abst = tf.thirteenf_emissions(
                        adds, resolve_fn=self._resolver.resolve, fund_name=fund_name,
                        period=latest["period"], filing_date=latest["filing_date"],
                        fund_cik=cik, accession=latest["accession"],
                        deny_list=self.deny_list, top_n=self.material_add_top_n,
                        kind="material_add")
                    out.extend(add_ems)
                    n_adds += len(add_ems)
                    abstained += add_res_abst
                    add_abstained += add_abst
                self._mark_processed(latest["accession"])   # processed even on empty diff
                processed += 1
            except Exception as e:  # noqa: BLE001 — isolate one bad fund; keep the rest
                errors += 1
                last_error = redact_secrets(str(e))
                continue

        tail = f", {abstained} unresolved CUSIPs" if abstained else ""
        if self.material_add_on:
            tail += f", {n_adds} material add(s)"
            # NOT "adds we abstained on": this counts every position held in BOTH books whose
            # share count was unusable or non-positive, tallied before the ratio test. It is
            # an `sshPrnamt` coverage diagnostic, so a 3-digit value on a large filer is not
            # in itself a fault.
            tail += (f" ({add_abstained} overlapping positions with unusable share counts)"
                     if add_abstained else "")
        tail += f", {errors} fund errors" if errors else ""
        if errors and last_error:
            tail += f" (last: {last_error})"    # surfaced, not dead state
        tail += "; filings-cap hit (carry-over)" if capped else ""
        tail += ("; " + "; ".join(skipped)) if skipped else ""   # loud, named skip notes
        self._status = (errors == 0,
                        f"{n_new} new 13F positions from {processed} filings"
                        f" ({len(self.funds)} funds){tail}")
        return out

    def cfg_key_for(self, emission) -> str:
        """Per-emission config key: material adds read `signals.edgar_13f_material_add`
        (weight 0.75), new positions read `signals.edgar_13f` (weight 1.0). Consumed by
        `daily.py:_scan_discovery` — only `weight` is resolved per kind; `max_slots` comes
        from `edgar_13f` and governs the whole family (see the asymmetry note there)."""
        from .thirteenf import SIGNAL_MATERIAL_ADD
        return ("edgar_13f_material_add" if emission.signal == SIGNAL_MATERIAL_ADD
                else "edgar_13f")

    def _mark_processed(self, accession: str) -> None:
        if accession and accession not in self.seen_accessions:
            self.seen_accessions.add(accession)
            self.processed_accessions.append(accession)

    def _fetch_infotable_retry(self, tf, cik, accession: str) -> list[dict]:
        """One infotable fetch + a single in-scan retry on empty (EDGAR is uncached, so a
        transient truncation heals on a second fetch — see the retry rationale in scan()).
        Still-empty after the retry is treated as genuinely unparseable, not transient."""
        rows = tf.fetch_infotable_rows(cik, accession, self.identity,
                                       timeout=self.timeout, throttle=self._throttle)
        if not rows:
            rows = tf.fetch_infotable_rows(cik, accession, self.identity,
                                           timeout=self.timeout, throttle=self._throttle)
        return rows

    def available(self) -> tuple[bool, str]:
        return self._status


register("edgar_13f", EdgarThirteenFSignal)


class EdgarBuybackSignal:
    """New-repurchase-authorization discovery from EDGAR full-text search (EFTS; data/efts.py).

    Surfaces 8-Ks whose full text matches a verb-anchored authorization phrase (default set
    in scout/buyback.py; e.g. "approved a new share repurchase program"). A DEFENSIBLE
    academic prior (Ikenberry-Lakonishok-Vermaelen 1995; Peyer-Vermaelen 2009 — positive
    post-announcement drift), but shipped DISABLED at weight 0.5 on the 8-K MEASURE-FIRST
    precedent: the sign in THIS funnel's universe/horizon is what the pre-registered backfill
    cohort (preregister/edgar_buyback_auth.yaml) earns or kills. Keyless + VPS-safe (SEC-hosted,
    no Yahoo WAF). EFTS lags a day or two, so scan() walks session-2..session PER PHRASE and
    dedups via a capped accession-seen set in ScoutState (daily.py persists new_accessions).
    """
    name = "edgar_buyback"
    is_discovery = True

    def __init__(self, identity: str | None = None, *, phrases=None, deny_list=None,
                 drop_spacs: bool = True, daily_cap: int = 6, lookback_days: int = 2,
                 seen_accessions: list[str] | None = None,
                 cache_dir: str | None = None,
                 resolver_cache_dir: str = ".cache/sec_tickers") -> None:
        from .buyback import DEFAULT_PHRASES
        self.identity = identity or "shortlist-scout turgechr@duck.com"
        self.phrases = list(phrases or DEFAULT_PHRASES)
        self.deny_list = list(deny_list or [])
        self.drop_spacs = drop_spacs
        self.daily_cap = daily_cap
        self.lookback_days = lookback_days
        self.seen_accessions = set(seen_accessions or [])
        self.cache_dir = cache_dir  # None => the efts BUYBACK_CACHE_DIR default
        self.resolver_cache_dir = resolver_cache_dir
        self._resolver: dict[str, str] | None = None
        self.new_accessions: list[str] = []   # daily.py persists these into ScoutState
        self._status = (False, "not run")

    def scan(self, session: date) -> list[Emission]:
        from ..data import efts
        from .buyback import buyback_events_from_rows
        from .cik_tickers import load_cik_to_ticker, resolve_ticker
        cache_kw = {} if self.cache_dir is None else {"cache_dir": self.cache_dir}
        try:
            if self._resolver is None:
                self._resolver = load_cik_to_ticker(self.identity,
                                                    cache_dir=self.resolver_cache_dir)
            if self._resolver == {}:
                # An empty CIK->ticker resolver would abstain on EVERY row (no display_names
                # fallback), yet a naive run would still record new_accessions and permanently
                # suppress those authorizations. Mirror the 13F degraded guard: skip, record
                # nothing, drop the memoized resolver so "retry next session" is real.
                self._resolver = None
                self.new_accessions = []
                self._status = (False, _CIK_RESOLVER_EMPTY_MSG)
                return []
            resolver = self._resolver
            rows: list[dict] = []
            failed: list[str] = []
            # EFTS lags (today often total: 0) — walk back session-2..session for each phrase;
            # the accession-seen state below stops the overlap re-emitting across runs. Each
            # (phrase, day) is day-cached in its own phrase-hash namespace.
            for phrase in self.phrases:
                for back in range(self.lookback_days, -1, -1):
                    day = session - timedelta(days=back)
                    got = efts.fetch_phrase_day(phrase, day, identity=self.identity,
                                                **cache_kw)
                    if got is None:
                        failed.append(f"{day.isoformat()}")
                    else:
                        rows.extend({**r, "phrase": phrase} for r in got)
        except Exception as e:  # noqa: BLE001 — degrade, never crash the run
            self._status = (False, redact_secrets(str(e)))
            return []
        ems = buyback_events_from_rows(
            rows, resolve_ticker_fn=lambda cik: resolve_ticker(cik, resolver),
            deny_list=self.deny_list, drop_spacs=self.drop_spacs)
        fresh = [e for e in ems if e.meta.get("adsh") not in self.seen_accessions]
        # EFTS returns RELEVANCE order when q is present — sort by file_date desc so the cap
        # keeps the FRESHEST authorizations, not an older higher-scoring one (design §2.5).
        fresh.sort(key=lambda e: e.meta.get("file_date") or "", reverse=True)
        capped = fresh[:self.daily_cap]
        overflow = [e.ticker for e in fresh[self.daily_cap:]]
        # Persist each emitted winner's own accession AND any same-(ticker, day) siblings the
        # leaf suppressed — so an unstable-relevance-order later run can't let the losing
        # sibling win and double-emit the same authorization (capped emissions only).
        self.new_accessions = []
        for e in capped:
            self.new_accessions.append(e.meta["adsh"])
            self.new_accessions.extend(e.meta.get("sibling_adsh", []))
        # De-dup the failed-day list (phrases share the walk-back days).
        failed_days = sorted(set(failed))
        tail = f"; failed days: {', '.join(failed_days)}" if failed_days else ""
        over = f"; overflow past cap: {', '.join(overflow)}" if overflow else ""
        self._status = (not failed_days,
                        f"{len(capped)} buyback auths from {len(rows)} hits "
                        f"(cap {self.daily_cap}){over}{tail}")
        return capped

    def available(self) -> tuple[bool, str]:
        return self._status


register("edgar_buyback", EdgarBuybackSignal)


class WsbHypeSignal:
    """WSB hype discovery via ApeWisdom — surfaces tickers whose mention velocity is
    rising above an absolute floor (emerging hype, not perennial mega-cap chatter)."""
    name = "wsb_hype"
    is_discovery = True

    def __init__(self, cache_dir: str = ".cache/apewisdom", min_mentions: int = 30,
                 min_mention_delta_pct: float = 0.5, top_n: int = 15,
                 deny_list: list[str] | None = None,
                 novelty: dict | None = None) -> None:
        self.cache_dir = cache_dir
        self.min_mentions = min_mentions
        self.min_mention_delta_pct = min_mention_delta_pct
        self.top_n = top_n
        self._deny_raw = list(deny_list or [])
        # `novelty` absent or disabled -> the velocity path below runs unchanged and this
        # class is byte-identical to its pre-feature behaviour (pinned by test).
        self.novelty = dict(novelty or {})
        self._status = (False, "not run")

    def scan(self, session: date) -> list[Emission]:
        from ..data.apewisdom import fetch_wsb_mentions, norm_symbol
        deny = {norm_symbol(d) for d in self._deny_raw}
        idx, err = fetch_wsb_mentions(self.cache_dir)
        if err:
            self._status = (False, redact_secrets(err))
            return []
        if self.novelty.get("enabled"):
            return self._scan_novelty(session, idx, deny)
        # Discovery requires a measurable 24h baseline (mention_delta_pct is not None):
        # unlike the advisory social_hype flag, a brand-new spike with no baseline is
        # NOT surfaced here — discovery needs evidence of velocity, not just volume.
        hot = [w for w in idx.values()
               if norm_symbol(w.ticker) not in deny
               and (w.mentions or 0) >= self.min_mentions
               and w.rising
               and w.mention_delta_pct is not None
               and w.mention_delta_pct >= self.min_mention_delta_pct]
        hot.sort(key=lambda w: w.mention_delta_pct or 0.0, reverse=True)
        hot = hot[:self.top_n]
        ems = []
        for w in hot:
            strength = max(0.0, min(1.0, (w.mention_delta_pct or 0.0) / 3.0))   # +300% -> 1.0
            ev = f"WSB: {w.mentions} mentions, {w.mention_delta_pct:+.0%} 24h, rank {w.rank}"
            ems.append(Emission(w.ticker, "wsb:hype", strength, ev, is_discovery=True))
        self._status = (True, f"{len(ems)} hyped (from {len(idx)} tracked)")
        return ems

    def _scan_novelty(self, session: date, idx: dict, deny: set) -> list[Emission]:
        """Rank-novelty path: emit names that are NOT board regulars (`wsb_novelty`).

        Emits under a DISTINCT signal string, `wsb:novel`. The velocity rule's ~82 live
        picks are already pooled in the firehose and the selection ledger under
        `wsb:hype`; reusing that key would blend two different populations into one
        cohort and make the ledger permanently unable to measure either. Same reasoning
        that gave the 8-K negative veto its own `edgar:8k_negative` key.
        """
        from ..data.apewisdom import read_cached_boards
        from .wsb_novelty import (
            DEFAULT_LOOKBACK_DAYS,
            DEFAULT_MAX_REGULAR_RANK,
            DEFAULT_MIN_HISTORY_DAYS,
            DEFAULT_MIN_MENTIONS,
            qualify_board,
        )

        cfg = self.novelty
        lookback = int(cfg.get("lookback_days", DEFAULT_LOOKBACK_DAYS))
        boards = read_cached_boards(self.cache_dir, before=session.isoformat(),
                                    lookback_days=lookback)
        rows, detail = qualify_board(
            idx, boards,
            max_regular_rank=int(cfg.get("max_regular_rank", DEFAULT_MAX_REGULAR_RANK)),
            min_mentions=int(cfg.get("min_mentions", DEFAULT_MIN_MENTIONS)),
            min_history_days=int(cfg.get("min_history_days", DEFAULT_MIN_HISTORY_DAYS)),
            top_n=self.top_n, deny=frozenset(deny))
        if rows is None:
            # Abstention is NOT a failure: `models.run_health` treats an enabled discovery
            # signal with ran=False as a failed originator and marks the whole run
            # degraded. A cold cache would then report every run for `min_history_days`
            # sessions as broken. Report ran=True with zero emissions instead.
            self._status = (True, f"0 novel — {detail}")
            return []
        ems = []
        for ticker, verdict, mentions in rows:
            seen = ("new to the board" if verdict.best_prior_rank is None
                    else f"prior best rank {verdict.best_prior_rank}")
            ev = f"WSB novel: {mentions} mentions, {seen}"
            ems.append(Emission(ticker, "wsb:novel", verdict.strength, ev,
                                is_discovery=True,
                                meta={"mentions": mentions,
                                      "best_prior_rank": verdict.best_prior_rank,
                                      "rule": "novelty_v1"}))
        self._status = (True, detail)
        return ems

    def available(self) -> tuple[bool, str]:
        return self._status


register("wsb_hype", WsbHypeSignal)


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
