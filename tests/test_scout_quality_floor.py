"""Deep-screen slot hygiene: don't spend one of ~10 daily FMP slots on a name that cannot
be a good buy under any reading.

Deliberately a FLOOR, not a ranker. An adversarial review (2026-08-05) noted that a
full-universe fundamental RANKING is "the existing quality/value composite run at S&P-1500
scale" — the add-scoring-surface move this repo has killed four times. Dropping the
structurally unfit is a different, defensible claim: it needs no return study, only the
observation that the slot is wasted.
"""
from shortlist.scout.quality_floor import Fundamentals, Verdict, assess


def _f(**kw):
    base = dict(cik="0000000001", revenue=100.0, net_income=10.0,
                equity=50.0, assets=200.0, ocf=20.0)
    base.update(kw)
    return Fundamentals(**base)


def test_a_healthy_business_passes():
    assert assess(_f()).keep is True


def test_zero_or_negative_revenue_is_dropped_as_a_shell():
    """A pre-revenue/shell filer has no business to assess; the scorer abstains on most of
    its legs anyway, so the slot buys nothing."""
    for rev in (0.0, -5.0):
        v = assess(_f(revenue=rev))
        assert v.keep is False and "revenue" in v.reason.lower()


def test_negative_equity_with_POSITIVE_earnings_is_KEPT():
    """THE load-bearing guard. Sustained buybacks drive book equity negative at healthy,
    profitable compounders — CLAUDE.md records this exact trap for the over_leveraged gate,
    where an unguarded D/E rule would have flagged them. Negative equity ALONE must never
    drop a name."""
    v = assess(_f(equity=-40.0, net_income=25.0))
    assert v.keep is True


def test_negative_equity_AND_negative_earnings_AND_cash_burn_is_dropped():
    v = assess(_f(equity=-40.0, net_income=-25.0, ocf=-8.0))
    assert v.keep is False and "equity" in v.reason.lower()


def test_a_REIT_shaped_filer_with_POSITIVE_operating_cash_flow_is_KEPT():
    """Second load-bearing guard. REITs routinely carry negative book equity AND negative
    GAAP earnings purely from depreciation, while generating real cash — GIPR in the live
    selection ledger is exactly this shape. Requiring cash burn too separates 'accounting
    losses from a non-cash charge' from 'actually burning cash', without needing a sector
    lookup the live path cannot cheaply do."""
    assert assess(_f(equity=-40.0, net_income=-25.0, ocf=+12.0)).keep is True


def test_absent_operating_cash_flow_abstains_rather_than_dropping():
    """Abstain-never-guess: without OCF we cannot tell a depreciating REIT from a zombie."""
    assert assess(_f(equity=-40.0, net_income=-25.0, ocf=None)).keep is True


def test_missing_data_always_abstains_to_KEEP():
    """Abstain, never guess: absent fundamentals are a coverage gap, not evidence of a bad
    business. Dropping on absence would silently bias the funnel toward well-tagged filers."""
    assert assess(_f(revenue=None)).keep is True
    assert assess(_f(equity=None, net_income=None)).keep is True
    assert assess(_f(revenue=None, net_income=None, equity=None, assets=None)).keep is True


def test_verdict_carries_a_human_reason_for_the_manifest():
    v = assess(_f(revenue=0.0))
    assert isinstance(v, Verdict) and v.reason and v.reason == v.reason.strip()


# --- funnel integration: mirrors apply_veto exactly -----------------------------------

from shortlist.scout.funnel import apply_quality_floor          # noqa: E402
from shortlist.scout.models import Candidate, Emission          # noqa: E402


def _cand(ticker):
    c = Candidate(ticker=ticker)
    c.add(Emission(ticker, "edgar:activist_13d", 1.0, "13D", is_discovery=True), 1.0)
    return c


def test_apply_quality_floor_with_no_fundamentals_is_the_identity():
    """Byte-identical pre-feature funnel when the feature is off or data is unavailable."""
    cands = [_cand("AAA"), _cand("BBB")]
    kept, dropped = apply_quality_floor(cands, {})
    assert [c.ticker for c in kept] == ["AAA", "BBB"] and dropped == []


def test_apply_quality_floor_drops_only_the_unfit_and_reports_them():
    cands = [_cand("GOOD"), _cand("SHELL"), _cand("UNKNOWN")]
    verdicts = {"SHELL": Verdict(keep=False, reason="no revenue")}
    kept, dropped = apply_quality_floor(cands, verdicts)
    assert [c.ticker for c in kept] == ["GOOD", "UNKNOWN"]   # unmapped name abstains -> kept
    assert [c.ticker for c, _ in dropped] == ["SHELL"]
    assert dropped[0][1].reason == "no revenue"


def test_apply_quality_floor_is_case_insensitive_on_ticker():
    kept, dropped = apply_quality_floor([_cand("shell")],
                                        {"SHELL": Verdict(keep=False, reason="no revenue")})
    assert kept == [] and len(dropped) == 1


# --- pure assembly from frames --------------------------------------------------------

from shortlist.data.secframes import Frame                                    # noqa: E402
from shortlist.scout.quality_floor import (fundamentals_from_frames,          # noqa: E402
                                           verdicts_from_fundamentals)


def _fr(v):
    return Frame(val=v, end="2025-12-31", accn="a")


def test_fundamentals_from_frames_joins_on_cik_and_keys_on_ticker():
    funds = fundamentals_from_frames(
        cik_to_ticker={"0000000001": "AAA", "0000000002": "BBB"},
        revenue={"0000000001": _fr(100.0)},
        net_income={"0000000001": _fr(10.0), "0000000002": _fr(-5.0)},
        equity={"0000000002": _fr(-1.0)},
        assets={})
    assert funds["AAA"].revenue == 100.0 and funds["AAA"].net_income == 10.0
    assert funds["AAA"].equity is None            # absent -> None, never 0.0
    assert funds["BBB"].equity == -1.0


def test_fundamentals_from_frames_ignores_ciks_with_no_ticker():
    """An unlisted filer cannot be a candidate, so it must not occupy the map."""
    funds = fundamentals_from_frames(cik_to_ticker={"0000000001": "AAA"},
                                     revenue={"0000000009": _fr(5.0)},
                                     net_income={}, equity={}, assets={})
    assert "AAA" in funds and len(funds) == 1
    assert funds["AAA"].revenue is None


def test_verdicts_contains_ONLY_drops():
    """Absent-from-map is the single 'abstain' rule in apply_quality_floor, so emitting
    keeps as well would be redundant surface that could drift out of sync."""
    funds = {"GOOD": Fundamentals(cik="1", revenue=10.0, net_income=1.0),
             "SHELL": Fundamentals(cik="2", revenue=0.0),
             "THIN": Fundamentals(cik="3")}
    v = verdicts_from_fundamentals(funds)
    assert set(v) == {"SHELL"} and v["SHELL"].keep is False


# --- config-absence contract ----------------------------------------------------------

def test_removing_the_quality_floor_block_makes_the_sweep_fully_inert():
    """C-2 contract (the scout.form4 precedent): an absent or disabled block must do ZERO
    fetching, not merely return an empty result. The fetch double RAISES, so this passes
    only if the sweep returns before any network call is attempted."""
    from datetime import date

    from shortlist.scout.daily import _quality_floor_verdicts

    def _boom(*a, **k):
        raise AssertionError("quality floor must not fetch when disabled")

    for cfg in ({}, {"quality_floor": {}}, {"quality_floor": {"enabled": False}}):
        assert _quality_floor_verdicts(cfg, date(2026, 8, 5), _fetch=_boom) == ({}, [])


def test_enabled_floor_reports_drops_as_notes():
    from datetime import date

    from shortlist.scout.daily import _quality_floor_verdicts

    def _fetch(**kw):
        return {"SHELL": Fundamentals(cik="1", revenue=0.0),
                "GOOD": Fundamentals(cik="2", revenue=9.0, net_income=1.0)}

    verdicts, notes = _quality_floor_verdicts(
        {"quality_floor": {"enabled": True}}, date(2026, 8, 5), _fetch=_fetch)
    assert set(verdicts) == {"SHELL"}
    # universe-level note is a SUMMARY: naming drops belongs where candidates exist (run()),
    # since the universe has thousands of unfit filers and only a handful are ever candidates
    assert any("2 listed filers" in n for n in notes)


def test_dropped_candidates_are_named_individually_like_the_veto():
    """Observability parity with the 8-K veto: a slot-affecting drop must be attributable
    to a named ticker AND a reason in the manifest, never a bare count."""
    from shortlist.scout.daily import _quality_floor_notes

    notes = _quality_floor_notes([(_cand("SHELL"), Verdict(keep=False, reason="no revenue"))])
    assert len(notes) == 1
    assert "SHELL" in notes[0] and "no revenue" in notes[0]


def test_no_drops_produces_no_noise():
    from shortlist.scout.daily import _quality_floor_notes
    assert _quality_floor_notes([]) == []


def test_a_failed_universe_fetch_degrades_to_inert_with_a_LOUD_note():
    """Screening unprotected must never be silent (the veto-sweep precedent)."""
    from datetime import date

    from shortlist.scout.daily import _quality_floor_verdicts

    def _fetch(**kw):
        raise RuntimeError("SEC down")

    verdicts, notes = _quality_floor_verdicts(
        {"quality_floor": {"enabled": True}}, date(2026, 8, 5), _fetch=_fetch)
    assert verdicts == {}
    assert notes and any("quality floor" in n.lower() for n in notes)
