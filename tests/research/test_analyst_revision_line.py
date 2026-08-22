"""The /deep analyst-revision context line.

Prompt-only, never scored, never flagged, never in the grounding haystack. It renders
the CHANGE in sell-side rating counts and deliberately never the levels: the levels on
StockMetrics are merged across vendors while the deltas come from one Finnhub payload,
so printing them together would pair an FMP panel with a Finnhub one.
"""
from shortlist.research import analyst_revision

CFG = {"enabled": True}


class _M:
    """Minimal StockMetrics stand-in — the line reads only the revision fields."""

    def __init__(self, months=None, buy=None, hold=None, sell=None):
        self.rating_months = months
        self.rating_buy_delta = buy
        self.rating_hold_delta = hold
        self.rating_sell_delta = sell


# --- abstention ------------------------------------------------------------------

def test_abstains_when_disabled_or_config_absent():
    m = _M(3, -2, 1, 1)
    assert analyst_revision.context_line(m, None) is None
    assert analyst_revision.context_line(m, {"enabled": False}) is None


def test_abstains_without_a_window():
    """No months means no drift was derivable (one period, or undated rows)."""
    assert analyst_revision.context_line(_M(None, -2, 1, 1), CFG) is None
    assert analyst_revision.context_line(_M(0, -2, 1, 1), CFG) is None


# --- rendering ---------------------------------------------------------------------

def test_renders_the_deltas_and_the_net_change():
    line = analyst_revision.context_line(_M(3, -2, 1, 1), CFG)
    assert "3 months" in line
    assert "buy -2" in line and "hold +1" in line and "sell +1" in line
    assert "net -3" in line          # net = buy_delta - sell_delta


def test_never_renders_a_rating_level():
    """Levels are cross-vendor; the deltas are not. Pairing them would be a lie."""
    line = analyst_revision.context_line(_M(3, -2, 1, 1), CFG)
    for level in ("37", "14", "39", "13"):
        assert level not in line


def test_flat_consensus_renders_as_no_revision():
    """Silence would let the model infer change from an absent line. An explicit
    'unchanged' is the informative rendering."""
    line = analyst_revision.context_line(_M(4, 0, 0, 0), CFG)
    assert line is not None
    assert "unchanged" in line
    assert "4 months" in line


def test_singular_month():
    assert "1 month" in analyst_revision.context_line(_M(1, 1, 0, 0), CFG)


def test_missing_individual_deltas_are_treated_as_zero():
    line = analyst_revision.context_line(_M(3, -2, None, None), CFG)
    assert "buy -2" in line and "net -2" in line


def test_line_says_it_is_context_not_filing_text():
    """The per-segment grounding rule: a computed number must never look quotable."""
    line = analyst_revision.context_line(_M(3, -2, 1, 1), CFG)
    assert "context only" in line
    assert "not filing text" in line.lower()
