from __future__ import annotations

from abc import ABC, abstractmethod

from ..models import StockMetrics


class Provider(ABC):
    """A data source. Each provider populates only the fields it does well;
    the merger combines them by configured priority."""

    name: str = "base"

    @abstractmethod
    def fetch(self, ticker: str) -> StockMetrics:
        """Return a StockMetrics with whatever fields this source can supply.
        Unavailable fields must be left as None (never guessed)."""
        raise NotImplementedError

    def _tag(self, m: StockMetrics, *fields: str) -> StockMetrics:
        """Record which fields this provider supplied (for the merge audit trail)."""
        for f in fields:
            if getattr(m, f, None) is not None:
                m.sources[f] = self.name
        return m
