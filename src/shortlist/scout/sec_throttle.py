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

# SEC fair access is ~10 req/s. 0.34 s (~3 req/s) is the interval thirteenf.py already ran
# at in production without complaint, and leaves headroom for the harness EdgarSource,
# which fetches on its own asyncio semaphore outside this budget.
DEFAULT_MIN_INTERVAL_S = 0.34


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
