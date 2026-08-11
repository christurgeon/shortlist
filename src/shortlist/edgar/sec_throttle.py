"""One process-wide sec.gov rate budget, shared by every SEC consumer.

A per-caller throttle cannot bound the *process's* request rate: one unthrottled sweep
exhausts SEC's fair-access budget and every other caller takes 429s behind it. Never give a
client its own throttle.
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
    """Min-interval throttle + per-consumer request accounting. Thread-safe: callers run
    signals on one worker thread, but the bot and the harness can call in concurrently.

    Counting exists because the 2026-08-04 cascade (the Form 4 sweep starving 13D and DERA)
    is still INFERRED from timing correlation. Per-consumer counts turn that into evidence
    and size how much of the budget a new originator can safely take."""

    def __init__(self, min_interval_s: float = DEFAULT_MIN_INTERVAL_S) -> None:
        self.min_interval_s = min_interval_s
        self._lock = threading.Lock()
        self._last = 0.0
        self._counts: dict[str, int] = {}

    def __call__(self, consumer: str | None = None) -> None:
        """Acquire one slot. `consumer` labels the caller for the budget report; an
        unlabelled call is still COUNTED (as `unattributed`) so no request can vanish from
        the budget just because a call site predates the label."""
        with self._lock:
            key = consumer or "unattributed"
            self._counts[key] = self._counts.get(key, 0) + 1
            now = time.monotonic()
            wait = self.min_interval_s - (now - self._last)
            if wait > 0:
                time.sleep(wait)
            self._last = time.monotonic()

    @property
    def counts(self) -> dict[str, int]:
        with self._lock:
            return dict(self._counts)

    @property
    def total(self) -> int:
        with self._lock:
            return sum(self._counts.values())

    def reset_counts(self) -> None:
        """Zero the accounting (not the pacing). Called once at the start of a daily run."""
        with self._lock:
            self._counts.clear()


_shared = SecThrottle()


def sec_throttle() -> SecThrottle:
    """The process-wide throttle. Every sec.gov request should pass through it."""
    return _shared
