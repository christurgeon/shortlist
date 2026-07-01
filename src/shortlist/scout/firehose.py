"""Raw-signal firehose: the PRE-SCORER superset of every discovery event, recorded so a
signal's forward-return quality can be measured on the SIGNAL, not the signal+scorer bundle
(see docs/superpowers/specs/2026-07-01-signal-validation-harness-backfill-design.md §5).

CohortEvent is the shared cohort-record schema consumed by the Phase-1 evaluator; the live
firehose and the (deferred) Phase-2 backfill both emit this one shape.
"""
from __future__ import annotations

from dataclasses import asdict, dataclass, field
from datetime import date

from .models import Emission


@dataclass
class CohortEvent:
    signal: str                       # e.g. "edgar:activist_13d"
    ticker: str
    cik: str | None
    event_date: date                  # DERIVED: the run session (Emission carries no date;
                                      # the after-close walk-back makes the filing <=4 td older)
    as_of_price: float | None         # DERIVED later from Yahoo; None at emit time
    strength: float | None            # the signal's OWN magnitude (not the composite)
    gated: bool | None                # set only if the name reached scoring; else None
    composite: float | None           # scorer output if scored; else None
    origin: str                       # "live" | "backfill"
    meta: dict = field(default_factory=dict)

    def to_dict(self) -> dict:
        d = asdict(self)
        d["event_date"] = self.event_date.isoformat()
        return d


def cohort_events_from_emissions(emissions: list[Emission], session: date,
                                 origin: str = "live") -> list[CohortEvent]:
    """Map every fired Emission (pre-scorer) to a CohortEvent. Pure. `as_of_price`,
    `gated`, `composite` are left None — derived downstream, never fabricated here."""
    return [
        CohortEvent(
            signal=e.signal,
            ticker=e.ticker.upper(),
            cik=e.cik,
            event_date=session,
            as_of_price=None,
            strength=e.strength,
            gated=None,
            composite=None,
            origin=origin,
        )
        for e in emissions
    ]
