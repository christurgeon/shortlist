"""The shared sec.gov throttle (docs/audits/2026-08-05-discovery-funnel-audit.md §4).

Every scout SEC consumer must share ONE rate budget: `edgar_index` alone fetches up to
`edgar_index_daily_cap` (2500) filings per session, and when that sweep is unthrottled it
429s the SEC for the rest of the run — which is how the 13D originator lost two sessions.
"""
import time

from shortlist.edgar.sec_throttle import SecThrottle, sec_throttle


def test_throttle_enforces_the_minimum_interval_between_consecutive_calls():
    t = SecThrottle(min_interval_s=0.05)
    start = time.monotonic()
    for _ in range(4):
        t()
    # 4 calls => at least 3 gaps; the first call is free.
    assert time.monotonic() - start >= 0.05 * 3


def test_the_first_call_is_not_delayed():
    start = time.monotonic()
    SecThrottle(min_interval_s=5.0)()
    assert time.monotonic() - start < 1.0


def test_the_process_wide_throttle_is_one_shared_instance():
    """A per-signal throttle cannot bound the process's SEC request rate — the Form 4 sweep
    starving 13D/DERA in the same run is exactly that failure."""
    assert sec_throttle() is sec_throttle()


def test_the_shared_throttle_stays_under_the_sec_fair_access_ceiling():
    assert sec_throttle().min_interval_s > 0
    assert 1.0 / sec_throttle().min_interval_s <= 10.0     # SEC fair access ~10 req/s
def test_thirteenf_still_exports_SecThrottle_for_back_compat():
    from shortlist.edgar import thirteenf
    assert thirteenf.SecThrottle is SecThrottle


# --- request accounting (plan Phase 1.1) --------------------------------------------
# The 2026-08-04 cascade (Form 4 sweep starving 13D/DERA) is still INFERRED from timing
# correlation. Counting requests per consumer is what turns it into evidence, and it sizes
# how much budget a new originator can safely take.

def test_the_throttle_counts_acquisitions_per_consumer():
    t = SecThrottle(min_interval_s=0.0)
    for _ in range(3):
        t("edgar_form4")
    t("edgar_activist_13d")
    assert t.counts == {"edgar_form4": 3, "edgar_activist_13d": 1}
    assert t.total == 4


def test_an_unlabelled_call_is_counted_but_marked_unattributed():
    """Back-compat: existing `throttle()` call sites must keep working, and their requests
    must still appear in the budget rather than vanishing from it."""
    t = SecThrottle(min_interval_s=0.0)
    t()
    assert t.total == 1
    assert "unattributed" in t.counts


def test_counts_can_be_reset_per_run():
    t = SecThrottle(min_interval_s=0.0)
    t("a")
    t.reset_counts()
    assert t.counts == {} and t.total == 0


def test_company_tickers_fetch_draws_on_the_shared_budget():
    """cik_tickers hits www.sec.gov but bypassed the shared throttle entirely, so its
    requests were neither paced nor counted — while a DERA 429 in the same run was the
    evidence used to diagnose the cascade."""
    from datetime import date

    from shortlist.edgar import cik_tickers as ct

    calls = []

    class _Resp:
        def raise_for_status(self): pass
        def json(self): return {"0": {"cik_str": 320193, "ticker": "AAPL", "title": "A"}}

    class _Client:
        def get(self, url): return _Resp()

    import tempfile
    with tempfile.TemporaryDirectory() as d:
        ct.reset_resolver_cache()
        ct.load_cik_to_ticker("me@x.com", cache_dir=d, _today=date(2026, 8, 5),
                              _client=_Client(), _throttle=lambda c=None: calls.append(c))
    assert len(calls) == 1, "the company_tickers fetch must pass through the shared throttle"


