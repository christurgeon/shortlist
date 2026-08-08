from __future__ import annotations

from dataclasses import dataclass, field
from datetime import date

# Module-level constant so both Candidate and tests can reference it unambiguously.
INTEREST_CAP: float = 10.0


@dataclass
class Emission:
    """One signal firing for one ticker on one session."""
    ticker: str
    signal: str            # e.g. "yahoo:day_gainers", "edgar:form4_cluster_buy"
    strength: float        # 0..1, source-normalized
    evidence: str          # human-readable, for the report
    is_discovery: bool     # True = can originate an unknown ticker; False = confluence-only
    cik: str | None = None  # optional EDGAR CIK (carried by filing-based signals so the
                            # selection ledger can re-resolve a renamed ticker; None elsewhere)
    meta: dict = field(default_factory=dict)  # optional per-emission facts (e.g. the 8-K
                            # accession + matched items) — passed through to the firehose
                            # CohortEvent.meta; {} for signals that carry none (back-compat)


@dataclass
class Candidate:
    """A ticker plus every signal that flagged it; carries the composite interest."""
    ticker: str = ""
    emissions: list[Emission] = field(default_factory=list)
    _interest: float = field(default=0.0, repr=False)

    def add(self, emission: Emission, weight: float) -> None:
        self.emissions.append(emission)
        self._interest = min(INTEREST_CAP, self._interest + emission.strength * weight)

    @property
    def interest(self) -> float:
        return self._interest

    @property
    def has_discovery(self) -> bool:
        return any(e.is_discovery for e in self.emissions)


@dataclass
class SignalStatus:
    name: str
    ran: bool
    detail: str            # "42 hits" or "rate-limited" — the coverage line
    discovery: bool = True  # False for enrichment signals (finnhub_news, wikipedia) that
                            # annotate already-found tickers and can never raise `raw`.
                            # LAST field with a default — every existing 3-arg construction
                            # stays valid (same rule as RunManifest.vetoed).


def run_health(signals: list["SignalStatus"], raw: int) -> tuple[str, list[str]]:
    """Classify a run as `healthy` / `degraded` / `quiet`, plus the failed originators.

    A DEGRADED run and a genuinely QUIET one both produce `0 raw`, and until now they
    rendered identically — which is how the 13D originator stayed dead for two sessions
    (docs/audits/2026-08-05-discovery-funnel-audit.md §5d).

    - `degraded`: an ENABLED discovery signal did not run. Takes precedence even when other
      originators still found candidates — a partial failure must not hide behind `raw > 0`.
    - `quiet`: nothing failed and nothing was found. A real, unremarkable filing day.
    - `healthy`: nothing failed and candidates were found.

    Disabled signals are never failures (six ship disabled on evidence), and neither are
    enrichment signals, whose `ran=False` means the funnel handed them nothing to check.
    """
    failed = [s.name for s in signals
              if s.discovery and not s.ran and not s.detail.startswith("disabled")]
    if failed:
        return "degraded", failed
    return ("quiet" if raw == 0 else "healthy"), []


@dataclass
class RunManifest:
    """Persisted per-run record for observability (written under scout/)."""
    session: date
    signals: list[SignalStatus]
    raw: int
    after_dedup: int
    after_prefilter: int
    screened: int
    dropped_for_budget: int
    researched: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)
    vetoed: int = 0        # names dropped by the negative-8-K veto this run
    # Per-consumer sec.gov request counts for the run, from the shared `sec_throttle()`.
    # LAST field with a default — every existing keyword/positional constructor stays valid.
    # Durable on purpose: the 2026-08-04 cascade was diagnosed from timing correlation
    # alone, and a count that lives only in an overwritten log cannot settle it afterwards.
    sec_requests: dict = field(default_factory=dict)
    # Names dropped by a per-originator slot cap (a SUBSET of dropped_for_budget, which
    # keeps its meaning: everything not chosen). Separate because the two reasons differ —
    # "ranked below the cut" vs "its originator's quota was spent while it outranked names
    # that were kept". LAST field with a default, same rule as `sec_requests` above.
    capped: int = 0

    def to_dict(self) -> dict:
        return {
            "session": self.session.isoformat(),
            "signals": [{"name": s.name, "ran": s.ran, "detail": s.detail}
                        for s in self.signals],
            "funnel": {"raw": self.raw, "after_dedup": self.after_dedup,
                       "after_prefilter": self.after_prefilter, "screened": self.screened,
                       "dropped_for_budget": self.dropped_for_budget,
                       "vetoed": self.vetoed, "capped": self.capped},
            "researched": self.researched,
            "notes": self.notes,
            "sec_requests": dict(self.sec_requests),
        }
