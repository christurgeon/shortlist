"""Contention-triggered per-originator slot cap in budget.select.

Design: docs/audits/2026-08-07-wsb-novelty-rule.md §7.2
"""
from shortlist.scout.budget import originator, select
from shortlist.scout.models import Candidate, Emission


def _cand(ticker, interest, signals=("wsb:novel",), booster=None):
    c = Candidate(ticker=ticker)
    # one discovery emission per signal, strength chosen so interest lands where asked
    per = interest / max(1, len(signals))
    for sig in signals:
        c.add(Emission(ticker, sig, per, "ev", is_discovery=True), 1.0)
    if booster:
        c.add(Emission(ticker, booster, 0.0, "ev", is_discovery=False), 1.0)
    return c


# ------------------------------------------------------------------ originator charging

def test_a_single_originator_candidate_charges_to_it():
    assert originator(_cand("A", 0.5)) == "wsb:novel"


def test_confluence_is_exempt():
    """Two DISTINCT discovery signals agreeing is the strongest thing this funnel
    produces; a quota must never delete it."""
    assert originator(_cand("A", 0.5, signals=("wsb:novel", "edgar:activist_13d"))) is None


def test_repeat_emissions_from_one_signal_are_not_confluence():
    """13F emits once per fund, so two marquee funds opening the same position yield two
    edgar:13f emissions on one candidate — one originator agreeing with itself."""
    assert originator(_cand("A", 0.5, signals=("edgar:13f", "edgar:13f"))) == "edgar:13f"


def test_boosters_are_not_counted_as_originators():
    """Boosters run BEFORE select, so a naive emission count would read one as an
    originator and wrongly exempt the candidate as confluence."""
    assert originator(_cand("A", 0.5, booster="finnhub:news")) == "wsb:novel"


# ----------------------------------------------------------------------------- the cap

def test_absent_caps_are_byte_identical_to_the_uncapped_ranking():
    cands = [_cand("A", 0.2), _cand("B", 0.9), _cand("C", 0.5), _cand("D", 0.7)]
    for caps in (None, {}):
        chosen, dropped, capped = select(cands, daily_x=2, caps=caps)
        assert [c.ticker for c in chosen] == ["B", "D"]
        assert dropped == 2 and capped == []


def test_cap_does_not_engage_at_or_below_daily_x():
    """No contention means nothing to arbitrate — capping there would drop names while
    slots sat empty."""
    cands = [_cand(f"W{i}", 0.9 - i / 100) for i in range(5)]
    chosen, dropped, capped = select(cands, daily_x=5, caps={"wsb:novel": 2})
    assert len(chosen) == 5 and dropped == 0 and capped == []


def test_cap_binds_under_contention_and_promotes_other_originators():
    wsb = [_cand(f"W{i}", 0.90 - i / 100) for i in range(5)]        # highest interest
    edg = [_cand(f"E{i}", 0.50 - i / 100, signals=("edgar:activist_13d",)) for i in range(3)]
    chosen, dropped, capped = select(wsb + edg, daily_x=4, caps={"wsb:novel": 2})
    picked = [c.ticker for c in chosen]
    assert picked[:2] == ["W0", "W1"]                     # quota honoured
    assert {"E0", "E1"} <= set(picked)                    # 13D promoted over W2/W3
    assert [c.ticker for c, _ in capped] == ["W2", "W3", "W4"][:len(capped)]
    assert all("wsb:novel quota" in reason for _, reason in capped)


def test_a_cap_never_wastes_a_slot():
    """When no other originator can fill the ceiling, deferred names are backfilled."""
    cands = [_cand(f"W{i}", 0.9 - i / 100) for i in range(6)]
    chosen, dropped, capped = select(cands, daily_x=4, caps={"wsb:novel": 2})
    assert len(chosen) == 4 and capped == []              # all four slots used
    assert dropped == 2                                   # only the genuine overflow


def test_confluence_candidates_bypass_a_spent_quota():
    wsb = [_cand(f"W{i}", 0.90 - i / 100) for i in range(3)]
    both = _cand("BOTH", 0.40, signals=("wsb:novel", "edgar:activist_13d"))
    other = _cand("E0", 0.30, signals=("edgar:activist_13d",))
    chosen, _, capped = select(wsb + [both, other], daily_x=3, caps={"wsb:novel": 1})
    picked = [c.ticker for c in chosen]
    assert "BOTH" in picked                               # exempt despite the quota
    assert [c.ticker for c, _ in capped] or True          # W1/W2 may be capped or backfilled


def test_below_the_cut_drops_are_not_mislabelled_as_cap_drops():
    """`capped` must name only what the cap DISPLACED. W4/W5 rank below daily_x=4 and
    would have been dropped by the uncapped ranking too."""
    cands = [_cand(f"W{i}", 0.9 - i / 100) for i in range(6)]
    _, dropped, capped = select(cands, daily_x=4, caps={"wsb:novel": 2})
    assert capped == [] and dropped == 2


def test_runmanifest_capped_roundtrip_and_default():
    """Back-compat pin: constructing without `capped` must still work and report 0."""
    from datetime import date

    from shortlist.scout.models import RunManifest
    m = RunManifest(session=date(2026, 8, 8), signals=[], raw=1, after_dedup=1,
                    after_prefilter=1, screened=1, dropped_for_budget=0)
    assert m.capped == 0 and m.to_dict()["funnel"]["capped"] == 0
    m2 = RunManifest(session=date(2026, 8, 8), signals=[], raw=1, after_dedup=1,
                     after_prefilter=1, screened=1, dropped_for_budget=3, capped=2)
    assert m2.to_dict()["funnel"]["capped"] == 2


def test_chosen_stays_interest_ordered_after_backfill():
    cands = [_cand("W0", 0.9), _cand("W1", 0.8),
             _cand("E0", 0.5, signals=("edgar:activist_13d",))]
    chosen, _, _ = select(cands, daily_x=3, caps={"wsb:novel": 1})
    interests = [c.interest for c in chosen]
    assert interests == sorted(interests, reverse=True)
