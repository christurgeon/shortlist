import json

from shortlist.edgar.cik_tickers import (build_cik_to_ticker, load_cik_to_ticker,
                                         reset_resolver_cache, resolve_ticker)


def _raw(rows):
    # company_tickers.json shape: {"0": {"cik_str": int, "ticker": str, "title": str}, ...}
    return {str(i): {"cik_str": c, "ticker": t, "title": t} for i, (c, t) in enumerate(rows)}


def test_first_occurrence_is_authoritative():
    # Common stock listed first must win over a later warrant/unit/right sibling.
    raw = _raw([(2088626, "PECE"), (2088626, "PECEU"), (2088626, "PECER"), (2088626, "PECEW")])
    idx = build_cik_to_ticker(raw)
    assert resolve_ticker("0002088626", idx) == "PECE"
    assert resolve_ticker(2088626, idx) == "PECE"   # int + padded resolve identically


def test_never_prefers_foreign_or_preferred_sibling_over_first():
    # The blanket-suffix bug: EQNR (first/common) must NOT lose to STOHF (a *F pink sibling).
    raw = _raw([(1234567, "EQNR"), (1234567, "STOHF")])
    assert resolve_ticker(1234567, build_cik_to_ticker(raw)) == "EQNR"
    # And a preferred sibling never displaces the common.
    raw2 = _raw([(70858, "BAC"), (70858, "BAC-PB")])
    assert resolve_ticker(70858, build_cik_to_ticker(raw2)) == "BAC"


def test_sibling_relative_backstop_only():
    # If a unit/warrant is (wrongly) first AND its base is also a ticker of the SAME cik,
    # prefer the base. BAYAU -> BAYA because BAYA exists for that cik.
    raw = _raw([(999001, "BAYAU"), (999001, "BAYA"), (999001, "BAYAR")])
    assert resolve_ticker(999001, build_cik_to_ticker(raw)) == "BAYA"


def test_never_rejects_sole_ticker_even_if_suffixed():
    # LW is a legitimate common ticker ending in W; with no sibling, keep it.
    raw = _raw([(1679273, "LW")])
    assert resolve_ticker(1679273, build_cik_to_ticker(raw)) == "LW"


def test_unmapped_cik_returns_none():
    assert resolve_ticker(424242, build_cik_to_ticker(_raw([(1, "AAA")]))) is None


def test_malformed_row_skipped_individually_good_rows_survive():
    """One malformed row among good ones must be skipped INDIVIDUALLY (not discard the whole
    ~12k-row index) — the all-or-nothing try/except used to silently zero the resolver."""
    raw = {
        "0": {"cik_str": 320193, "ticker": "AAPL", "title": "Apple Inc"},   # good
        "1": {"cik_str": None, "ticker": "BAD", "title": "Null Cik"},       # bad: null cik
        "2": {"cik_str": "nope", "ticker": "BAD2", "title": "Non-int Cik"}, # bad: non-int cik
        "3": {"ticker": "BAD3", "title": "Missing Cik Key"},                # bad: no cik_str
        "4": {"cik_str": 789019, "ticker": "MSFT", "title": "Microsoft"},   # good
    }
    idx = build_cik_to_ticker(raw)
    assert resolve_ticker(320193, idx) == "AAPL"         # good rows survive
    assert resolve_ticker(789019, idx) == "MSFT"
    assert len(idx) == 2                                 # only the 3 malformed rows dropped


# --- load_cik_to_ticker (cache / fetch / never-raise) ---
from datetime import date


class _FakeResp:
    def __init__(self, payload):
        self._payload = payload

    def raise_for_status(self):
        pass

    def json(self):
        return self._payload


class _FakeClient:
    def __init__(self, payload, *, headers=None):
        self._payload = payload
        self.headers = headers or {}
        self.requested = None

    def get(self, url):
        self.requested = url
        return _FakeResp(self._payload)


def test_load_reads_day_cache_without_network(tmp_path):
    day = date(2026, 6, 28)
    cp = tmp_path / f"company_tickers-{day.isoformat()}.json"
    cp.write_text(json.dumps(_raw([(70858, "BAC"), (70858, "BAC-PB")])))
    # A client that would explode if called proves the cache path makes no network call.
    boom = type("Boom", (), {"get": lambda self, u: (_ for _ in ()).throw(AssertionError("network!"))})()
    idx = load_cik_to_ticker("me@x.com", cache_dir=str(tmp_path), _today=day, _client=boom)
    assert resolve_ticker(70858, idx) == "BAC"


def test_load_fetches_and_caches_with_user_agent(tmp_path):
    day = date(2026, 6, 28)
    client = _FakeClient(_raw([(2088626, "PECE"), (2088626, "PECEW")]))
    idx = load_cik_to_ticker("me@x.com", cache_dir=str(tmp_path), _today=day, _client=client)
    assert resolve_ticker(2088626, idx) == "PECE"
    assert "company_tickers.json" in client.requested
    # cache file written for the day -> a second call must not hit the (boom) network
    assert (tmp_path / f"company_tickers-{day.isoformat()}.json").exists()


def test_load_never_raises_on_failure(tmp_path):
    class _BoomClient:
        headers = {}

        def get(self, url):
            raise RuntimeError("SEC 503 ?apikey=SECRET")

    idx = load_cik_to_ticker("me@x.com", cache_dir=str(tmp_path), _today=date(2026, 6, 28),
                             _client=_BoomClient(), _sleep=lambda s: None)
    assert idx == {}


class _FlakyClient:
    """Fails `fail_times` times, then serves `payload`. Records attempt count."""

    def __init__(self, payload, fail_times):
        self._payload = payload
        self._fail_times = fail_times
        self.attempts = 0

    def get(self, url):
        self.attempts += 1
        if self.attempts <= self._fail_times:
            raise RuntimeError("SEC 429 Too Many Requests")
        return _FakeResp(self._payload)


def test_load_falls_back_to_the_newest_cached_index_when_the_fetch_fails(tmp_path):
    """The 2026-08-04 outage: one transient SEC 429 returned {} and bailed EVERY
    resolver-backed originator for the session, while a valid 24h-old index sat unread on
    disk. A recent cached index must be preferred over abstaining."""
    (tmp_path / "company_tickers-2026-08-03.json").write_text(
        json.dumps(_raw([(320193, "AAPL")])))
    idx = load_cik_to_ticker("me@x.com", cache_dir=str(tmp_path), _today=date(2026, 8, 4),
                             _client=_FlakyClient({}, fail_times=99), _sleep=lambda s: None)
    assert resolve_ticker(320193, idx) == "AAPL"


def test_load_picks_the_most_recent_stale_index_not_just_any(tmp_path):
    (tmp_path / "company_tickers-2026-07-30.json").write_text(
        json.dumps(_raw([(1, "OLD")])))
    (tmp_path / "company_tickers-2026-08-03.json").write_text(
        json.dumps(_raw([(1, "NEWER")])))
    idx = load_cik_to_ticker("me@x.com", cache_dir=str(tmp_path), _today=date(2026, 8, 4),
                             _client=_FlakyClient({}, fail_times=99), _sleep=lambda s: None)
    assert resolve_ticker(1, idx) == "NEWER"


def test_load_ignores_a_cached_index_older_than_the_staleness_ceiling(tmp_path):
    """An index stale by months can mis-resolve renamed/delisted symbols — past the ceiling
    abstaining is correct again."""
    (tmp_path / "company_tickers-2026-01-02.json").write_text(
        json.dumps(_raw([(320193, "AAPL")])))
    idx = load_cik_to_ticker("me@x.com", cache_dir=str(tmp_path), _today=date(2026, 8, 4),
                             _client=_FlakyClient({}, fail_times=99), _sleep=lambda s: None)
    assert idx == {}


def test_load_retries_a_transient_failure_before_giving_up(tmp_path):
    client = _FlakyClient(_raw([(320193, "AAPL")]), fail_times=2)
    idx = load_cik_to_ticker("me@x.com", cache_dir=str(tmp_path), _today=date(2026, 8, 4),
                             _client=client, _sleep=lambda s: None)
    assert resolve_ticker(320193, idx) == "AAPL"
    assert client.attempts == 3          # two failures retried, third served


def test_every_call_site_in_a_run_sees_one_consistent_index(tmp_path):
    """signals.py loads the resolver at 5 independent call sites. Without an in-process
    memo they can DISAGREE inside one session: an early site fetches fresh, then SEC starts
    429ing and a later site silently falls back to an older index — so 13D and 8-K resolve
    the same CIK against different maps. The disk day-cache does not cover this (it is the
    thing that goes missing here); only a shared in-process result does."""
    reset_resolver_cache()
    client = _FlakyClient(_raw([(320193, "AAPL")]), fail_times=0)
    first = load_cik_to_ticker("me@x.com", cache_dir=str(tmp_path),
                               _today=date(2026, 8, 4), _client=client)
    assert resolve_ticker(320193, first) == "AAPL"

    # SEC now fails AND the day-cache is gone — a second call site must still see `first`.
    (tmp_path / "company_tickers-2026-08-04.json").unlink()
    later = load_cik_to_ticker("me@x.com", cache_dir=str(tmp_path), _today=date(2026, 8, 4),
                               _client=_FlakyClient({}, fail_times=99), _sleep=lambda s: None)
    assert later == first
    assert client.attempts == 1


def test_an_empty_result_is_never_memoised(tmp_path):
    """Caching a failure would pin the whole session to {} — the exact 08-04 outage."""
    reset_resolver_cache()
    boom = _FlakyClient({}, fail_times=99)
    assert load_cik_to_ticker("me@x.com", cache_dir=str(tmp_path), _today=date(2026, 8, 4),
                              _client=boom, _sleep=lambda s: None) == {}
    ok = _FlakyClient(_raw([(320193, "AAPL")]), fail_times=0)
    idx = load_cik_to_ticker("me@x.com", cache_dir=str(tmp_path), _today=date(2026, 8, 4),
                             _client=ok)
    assert resolve_ticker(320193, idx) == "AAPL"   # retried, not served from a cached {}


def test_load_never_raises_on_truthy_but_malformed_payload(tmp_path):
    """A truthy-but-malformed company_tickers.json body must degrade to {} — build_cik_to_ticker
    is inside the never-raises contract, so unwrapped callers (daily._build_scoreboard,
    symbology resolver) never crash the daily run."""
    day = date(2026, 6, 28)
    malformed = [
        {"0": {"cik_str": None, "ticker": "AAA", "title": "A"}},    # null cik_str
        {"0": {"cik_str": "not-an-int", "ticker": "AAA", "title": "A"}},  # non-int cik
        [{"cik_str": 1, "ticker": "AAA", "title": "A"}],            # list-shaped (no .values)
        {"0": {"ticker": "AAA"}},                                   # missing cik_str key
    ]
    for i, payload in enumerate(malformed):
        d = tmp_path / str(i)
        idx = load_cik_to_ticker("me@x.com", cache_dir=str(d), _today=day,
                                 _client=_FakeClient(payload))
        assert idx == {}
