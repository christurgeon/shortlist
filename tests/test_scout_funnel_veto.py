"""Veto funnel stage (funnel.apply_veto) + RunManifest.vetoed accounting. Pure — no
network, no state. The byte-identical-when-absent guarantee is pinned here at the funnel
layer (empty map == identity); the sweep's zero-fetch guarantee is pinned in
tests/test_scout_daily_veto.py."""
from datetime import date

from shortlist.scout.budget import select
from shortlist.scout.funnel import aggregate, apply_veto, prefilter
from shortlist.scout.models import Candidate, Emission, RunManifest, SignalStatus


def _cand(t, strength=0.9):
    c = Candidate(ticker=t)
    c.add(Emission(t, "edgar:form4_cluster_buy", strength, "ev", True), 1.0)
    return c


def test_apply_veto_empty_map_is_identity():
    cands = [_cand("AAA"), _cand("BBB")]
    kept, vetoed = apply_veto(cands, {})
    assert kept == cands and vetoed == []


def test_apply_veto_splits_kept_and_vetoed_case_insensitive():
    cands = [_cand("aaa"), _cand("BBB")]
    veto_map = {"AAA": {"last_date": "2026-07-01", "items": ["2.06"], "adsh": "n-1"}}
    kept, vetoed = apply_veto(cands, veto_map)
    assert [c.ticker for c in kept] == ["BBB"]
    assert [c.ticker for c in vetoed] == ["aaa"]


def test_vetoed_slot_backfills_in_select():
    """The veto's whole point: the dropped name never consumes a deep-screen slot — the
    next-ranked candidate takes it."""
    strong, weak = _cand("BAD", 0.9), _cand("OK", 0.4)
    kept, vetoed = apply_veto([strong, weak], {"BAD": {"adsh": "n-1"}})
    chosen, dropped = select(kept, daily_x=1)
    assert [c.ticker for c in chosen] == ["OK"] and dropped == 0
    assert [c.ticker for c in vetoed] == ["BAD"]


def test_funnel_chain_byte_identical_without_veto_map():
    ems = [Emission("AAA", "edgar:form4_cluster_buy", 0.9, "ev", True),
           Emission("BBB", "edgar:form4_cluster_buy", 0.8, "ev", True)]
    cands = prefilter(aggregate(ems, {}), in_cooldown=lambda t: False,
                      is_held=lambda t: False)
    kept, vetoed = apply_veto(cands, {})
    assert kept == cands and vetoed == []
    assert select(kept, 10) == select(cands, 10)


def test_runmanifest_vetoed_roundtrip_and_default():
    m = RunManifest(session=date(2026, 7, 6), signals=[SignalStatus("x", True, "d")],
                    raw=5, after_dedup=4, after_prefilter=3, screened=2,
                    dropped_for_budget=0, vetoed=1)
    assert m.to_dict()["funnel"]["vetoed"] == 1
    m2 = RunManifest(session=date(2026, 7, 6), signals=[], raw=0, after_dedup=0,
                     after_prefilter=0, screened=0, dropped_for_budget=0)
    assert m2.vetoed == 0                                 # back-compat default
    assert m2.to_dict()["funnel"]["vetoed"] == 0
