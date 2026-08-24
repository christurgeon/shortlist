"""How the options surface reaches the model: as a PROMPT-ONLY line, never as a
grounding segment.

These are market prices. A model that could quote one through quote-verification would
have a market price "verified" as a filing fact, which makes `verified=True` mean the
opposite of what it says. Design: docs/audits/2026-08-24-options-surface-design.md §5.
"""
from __future__ import annotations

import datetime

from shortlist.research import options
from shortlist.research.assess import _build_user_prompt
from shortlist.research.models import FilingBundle, FilingText

TODAY = datetime.date(2026, 8, 24)
CFG = {"enabled": True, "delta_tolerance": 0.10, "max_atm_spread_pct": 40,
       "earnings_date_uncertainty_days": 8, "max_earnings_expiry_gap_days": 14,
       "max_stale_days": 5}


class _M:
    realized_vol = 0.25
    earnings_days_to_next = 60
    filing_events = None
    insider_recent = None
    financial_series = None
    short_pct_outstanding = None
    days_to_cover = None
    market_cap = None

    def __getattr__(self, name):        # every other StockMetrics scalar reads None
        return None


class _Card:
    metrics = _M()
    composite = None
    confidence = None
    gates: list = []
    flags: list = []

    def __getattr__(self, name):
        return None


def _bundle():
    tenk = FilingText("XYZ", "acc", "2026-02-20", business="biz", mda="mda",
                      risk_factors="rf")
    return FilingBundle(tenk=tenk, primary_accession="acc", cache_key="acc",
                        filing_date="2026-02-20")


def _surface():
    contracts = []
    for expiry in (datetime.date(2026, 9, 25), datetime.date(2026, 11, 6)):
        stamp = f"{expiry:%y%m%d}"
        contracts += [
            {"option": f"XYZ{stamp}P00090000", "bid": 1.0, "ask": 1.1, "iv": 0.30,
             "delta": -0.25},
            {"option": f"XYZ{stamp}C00110000", "bid": 1.0, "ask": 1.1, "iv": 0.28,
             "delta": 0.25},
            {"option": f"XYZ{stamp}P00100000", "bid": 3.0, "ask": 3.2, "iv": 0.29,
             "delta": -0.50},
            {"option": f"XYZ{stamp}C00100000", "bid": 3.0, "ask": 3.2, "iv": 0.29,
             "delta": 0.50},
        ]
    payload = {"data": {"symbol": "XYZ", "current_price": 100.0, "iv30": 25.0,
                        "last_trade_time": "2026-08-21T15:59:59",
                        "options": contracts}}
    return options.build_surface(payload, TODAY, CFG)


def _prompt(**kw):
    config = {"research": {"options": CFG}}
    return _build_user_prompt(_bundle(), config, _Card(), **kw)


def test_the_line_never_enters_the_grounding_haystack():
    """The invariant. A market price must not be quotable as filing text."""
    bundle = _bundle()
    line = options.context_line(_surface(), _M(), CFG, today=TODAY)
    assert line
    assert line not in bundle.haystack()
    assert all(line not in text for _, text in bundle.segments())


def test_the_line_is_rendered_in_the_prompt():
    prompt = _prompt(options_surface=_surface())
    assert "Options market (CBOE delayed quotes" in prompt
    assert "25-delta skew" in prompt


def test_the_line_renders_after_the_instruction_block_not_as_a_segment():
    """Grounding segments are the `=== ... ===` blocks BEFORE the 'Return at most'
    instruction. Anything after it is prompt-only context."""
    prompt = _prompt(options_surface=_surface())
    assert prompt.index("Return at most") < prompt.index("Options market (CBOE")
    assert "=== OPTIONS" not in prompt


def test_no_surface_leaves_the_prompt_byte_identical():
    """A name with no options, or a failed fetch, must cost nothing."""
    assert _prompt(options_surface=None) == _prompt()


def test_disabled_config_leaves_the_prompt_byte_identical():
    baseline = _build_user_prompt(_bundle(), {"research": {}}, _Card(),
                                  options_surface=_surface())
    disabled = _build_user_prompt(_bundle(), {"research": {"options": {"enabled": False}}},
                                  _Card(), options_surface=_surface())
    assert baseline == disabled


def test_realized_moves_reach_the_prompt():
    prompt = _prompt(options_surface=_surface(),
                     earnings_moves=[("2026-07-30", -7.4), ("2026-04-30", 3.2)])
    assert "8-K Item 2.02" in prompt
    assert "-7.4%" in prompt
