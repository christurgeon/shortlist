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

    def to_dict(self) -> dict:
        return {
            "session": self.session.isoformat(),
            "signals": [{"name": s.name, "ran": s.ran, "detail": s.detail}
                        for s in self.signals],
            "funnel": {"raw": self.raw, "after_dedup": self.after_dedup,
                       "after_prefilter": self.after_prefilter, "screened": self.screened,
                       "dropped_for_budget": self.dropped_for_budget},
            "researched": self.researched,
            "notes": self.notes,
        }
