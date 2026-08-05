"""The shared sec.gov throttle (docs/audits/2026-08-05-discovery-funnel-audit.md §4).

Every scout SEC consumer must share ONE rate budget: `edgar_index` alone fetches up to
`edgar_index_daily_cap` (2500) filings per session, and when that sweep is unthrottled it
429s the SEC for the rest of the run — which is how the 13D originator lost two sessions.
"""
import time

from shortlist.scout.sec_throttle import SecThrottle, sec_throttle


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


def test_the_13f_signal_draws_on_the_process_wide_budget_not_its_own():
    """It built its own SecThrottle, so its ~3 req/s ran *on top of* the Form 4 sweep's
    rate instead of inside one shared ceiling."""
    from shortlist.scout.signals import EdgarThirteenFSignal
    assert EdgarThirteenFSignal(identity="me@x.com")._throttle is sec_throttle()


def test_thirteenf_still_exports_SecThrottle_for_back_compat():
    from shortlist.scout import thirteenf
    assert thirteenf.SecThrottle is SecThrottle
