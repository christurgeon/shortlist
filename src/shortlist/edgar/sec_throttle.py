"""One process-wide sec.gov rate budget, shared by every SEC consumer.

A per-caller throttle cannot bound the *process's* request rate: one unthrottled sweep
exhausts SEC's fair-access budget and every other caller takes 429s behind it. Never give a
client its own throttle.
"""
from __future__ import annotations

import threading
import time

# SEC fair access is ~10 req/s. This runs at ~6 (0.167 s), 60% of the ceiling, because
# this IP has a history of 429s. The headroom also covers the harness EdgarSource, which
# fetches on its own semaphore outside this budget. `screen.py:main` starts the research
# phase (the metered consumers) only after run_harness returns, so the two do not overlap
# in practice, but nothing enforces that.
#
# Request latency is so low that an unthrottled serial loop far exceeds the ceiling, and
# a thread pool would buy nothing: the interval is the whole constraint. See
# `docs/audits/2026-08-05-discovery-funnel-audit.md` §4/§9.
DEFAULT_MIN_INTERVAL_S = 0.167


class SecThrottle:
    """Min-interval throttle + per-consumer request accounting. Thread-safe: callers run
    signals on one worker thread, but the bot and the harness can call in concurrently.

    Per-consumer counts show which caller uses how much of the budget."""

    def __init__(self, min_interval_s: float = DEFAULT_MIN_INTERVAL_S) -> None:
        self.min_interval_s = min_interval_s
        self._lock = threading.Lock()
        self._last = 0.0
        self._counts: dict[str, int] = {}

    def __call__(self, consumer: str | None = None) -> None:
        """Acquire one slot. `consumer` labels the caller for the budget report. An
        unlabelled call is counted as `unattributed`."""
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
