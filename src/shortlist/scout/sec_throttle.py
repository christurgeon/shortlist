"""One process-wide sec.gov rate budget, shared by every scout SEC consumer.

Why this is a module and not a per-signal object: a per-signal throttle cannot bound the
*process's* request rate. On 2026-08-03/04 the Form 4 sweep (up to `edgar_index_daily_cap`
= 2500 filings, one request each, previously unthrottled) exhausted SEC's fair-access
budget mid-run, and the 13D originator, the DERA quarterly index and `company_tickers.json`
all took 429s behind it — the 13D signal lost two full sessions. See
`docs/audits/2026-08-05-discovery-funnel-audit.md` §4.

`SecThrottle` originated in `thirteenf.py`, which already noted "a shared instance across
signals stays polite" — this module is that shared instance. `thirteenf` re-exports the
class for back-compat.
"""
from __future__ import annotations

import threading
import time

# SEC fair access is ~10 req/s. We run at ~6 (0.167 s) — 60% of the ceiling, NOT the 80%
# that pure throughput would argue for, because this IP has a RECENT throttling history
# (real DERA 429s on 2026-08-03/04). The headroom also covers the harness EdgarSource, which
# fetches on its own asyncio semaphore OUTSIDE this budget — it runs after discovery
# (`daily.py`: `_scan_discovery` completes before `run_harness`), so the two do not overlap,
# but nothing enforces that.
#
# Sizing (measured 2026-08-05): `full_text_submission()` latency is ~17 ms median, so a
# serial unthrottled loop reaches ~57 req/s — 5.7x over the ceiling. That is what made the
# Form 4 sweep starve every other SEC consumer in the run. Serialisation is NOT the
# bottleneck at this latency, so a thread pool would buy nothing; the interval is the whole
# constraint. See `docs/audits/2026-08-05-discovery-funnel-audit.md` §4/§9.
DEFAULT_MIN_INTERVAL_S = 0.167


class SecThrottle:
    """Min-interval throttle. Thread-safe: the scout runs signals on one worker thread, but
    the bot and the harness can call in concurrently."""

    def __init__(self, min_interval_s: float = DEFAULT_MIN_INTERVAL_S) -> None:
        self.min_interval_s = min_interval_s
        self._lock = threading.Lock()
        self._last = 0.0

    def __call__(self) -> None:
        with self._lock:
            now = time.monotonic()
            wait = self.min_interval_s - (now - self._last)
            if wait > 0:
                time.sleep(wait)
            self._last = time.monotonic()


_shared = SecThrottle()


def sec_throttle() -> SecThrottle:
    """The process-wide throttle. Every sec.gov request in the scout should pass through it."""
    return _shared
