"""Rank-novelty qualification for the WSB originator (scout/wsb_novelty.py + the signal).

Design + measured evidence: docs/audits/2026-08-07-wsb-novelty-rule.md
"""
from datetime import date

import pytest

from shortlist.data import apewisdom
from shortlist.scout.signals import WsbHypeSignal
from shortlist.scout.wsb_novelty import assess, board_regulars, qualify_board


def _row(ticker, rank=None, mentions=None):
    return {"ticker": ticker, "rank": rank, "mentions": mentions}


def _board(*pairs):
    return {t.upper(): _row(t, rank=r, mentions=m) for t, r, m in pairs}


# --------------------------------------------------------------------------- pure leaf

def test_board_regulars_keeps_the_best_rank_across_boards():
    boards = [_board(("AAPL", 12, 400)), _board(("AAPL", 3, 900)), _board(("AAPL", 30, 200))]
    assert board_regulars(boards)["AAPL"] == 3          # best == numerically lowest


def test_board_regulars_skips_rows_without_a_usable_rank():
    """A missing rank must not read as rank 0, which would make the name maximally
    regular and permanently unemittable."""
    boards = [_board(("XYZ", None, 100)), {"ABC": {"rank": "not-a-number", "mentions": 50}}]
    assert board_regulars(boards) == {}


def test_a_board_regular_never_qualifies():
    regulars = {"AAPL": 3}
    v = assess("AAPL", 900, regulars, max_regular_rank=50, min_mentions=20)
    assert v.qualifies is False and "board regular" in v.reason


def test_a_name_seen_but_never_prominent_qualifies():
    """Present in history yet always ranked worse than the threshold — the case a plain
    'absent from the board' test would miss."""
    v = assess("WEN", 120, {"WEN": 77}, max_regular_rank=50, min_mentions=20)
    assert v.qualifies is True and v.best_prior_rank == 77


def test_a_never_seen_name_qualifies_with_no_prior_rank():
    v = assess("NEWCO", 60, {"AAPL": 3}, max_regular_rank=50, min_mentions=20)
    assert v.qualifies is True and v.best_prior_rank is None


@pytest.mark.parametrize("mentions", [None, 0, 19])
def test_abstains_below_the_mention_floor_and_on_a_missing_count(mentions):
    v = assess("NEWCO", mentions, {}, min_mentions=20)
    assert v.qualifies is False


def test_deny_list_wins_over_novelty():
    assert assess("GLD", 500, {}, deny=frozenset({"GLD"})).qualifies is False


def test_strength_rises_with_volume_and_stays_inside_its_band():
    low = assess("A", 20, {}, min_mentions=20).strength
    high = assess("B", 500, {}, min_mentions=20).strength
    assert 0.3 <= low < high <= 1.0


def test_qualify_board_abstains_below_min_history_and_says_so():
    rows, detail = qualify_board(_board(("NEWCO", 5, 90)), [_board(("AAPL", 1, 900))],
                                 min_history_days=5)
    assert rows is None
    assert "insufficient history" in detail and "1 of 5" in detail


def test_qualify_board_never_silently_truncates():
    today = _board(*[(f"T{i}", 5 + i, 100 - i) for i in range(6)])
    prior = [_board(("AAPL", 1, 900))] * 5
    rows, detail = qualify_board(today, prior, top_n=2, min_history_days=5)
    assert len(rows) == 2
    assert "over top_n=2" in detail and "T5" in detail        # overflow is NAMED


# ------------------------------------------------------------------- signal integration

def _mention(ticker, mentions, rank):
    return apewisdom.WsbMention(ticker=ticker, mentions=mentions, rank=rank,
                                as_of="2026-08-08")


def _idx():
    return {apewisdom.norm_symbol(t): _mention(t, m, r)
            for t, m, r in [("AAPL", 900, 2), ("WEN", 120, 40), ("NEWCO", 60, 55)]}


def test_novelty_path_emits_a_distinct_signal_key(monkeypatch):
    """`wsb:novel`, never `wsb:hype`: the velocity rule's ~82 live picks are already
    pooled under that key, and one cohort key cannot measure two populations."""
    monkeypatch.setattr(apewisdom, "fetch_wsb_mentions", lambda *a, **k: (_idx(), None))
    monkeypatch.setattr(apewisdom, "read_cached_boards",
                        lambda *a, **k: [_board(("AAPL", 2, 900))] * 6)
    sig = WsbHypeSignal(novelty={"enabled": True, "min_mentions": 20,
                                 "max_regular_rank": 50, "min_history_days": 5})
    ems = sig.scan(date(2026, 8, 8))
    assert {e.ticker for e in ems} == {"WEN", "NEWCO"}      # AAPL is a board regular
    assert all(e.signal == "wsb:novel" and e.is_discovery for e in ems)
    assert all(e.strength > 0 for e in ems)                 # never enters select at 0.0


def test_abstention_reports_ran_true_so_the_run_is_not_marked_degraded(monkeypatch):
    """models.run_health treats an enabled discovery signal with ran=False as a FAILED
    originator and marks the whole run degraded. A cold cache is not a failure."""
    monkeypatch.setattr(apewisdom, "fetch_wsb_mentions", lambda *a, **k: (_idx(), None))
    monkeypatch.setattr(apewisdom, "read_cached_boards", lambda *a, **k: [])
    sig = WsbHypeSignal(novelty={"enabled": True, "min_history_days": 5})
    assert sig.scan(date(2026, 8, 8)) == []
    ran, detail = sig.available()
    assert ran is True and "insufficient history" in detail


def test_abstention_never_falls_back_to_the_velocity_rule(monkeypatch):
    """Falling back would reinstate the composition the rule exists to fix, on exactly
    the runs nobody is watching."""
    idx = {apewisdom.norm_symbol("AAPL"): apewisdom.WsbMention(
        ticker="AAPL", mentions=900, mentions_24h_ago=100, rank=1,
        mention_delta_pct=8.0, rising=True, as_of="2026-08-08")}
    monkeypatch.setattr(apewisdom, "fetch_wsb_mentions", lambda *a, **k: (idx, None))
    monkeypatch.setattr(apewisdom, "read_cached_boards", lambda *a, **k: [])
    sig = WsbHypeSignal(novelty={"enabled": True, "min_history_days": 5})
    assert sig.scan(date(2026, 8, 8)) == []      # the velocity rule would have emitted AAPL


def test_absent_novelty_block_leaves_the_velocity_rule_byte_identical(monkeypatch):
    """The config-absence contract. `read_cached_boards` RAISES here, so this passes only
    if the novelty path is never entered — it cannot pass as a tautology."""
    def _boom(*a, **k):
        raise AssertionError("read_cached_boards must not be called without a novelty block")

    monkeypatch.setattr(apewisdom, "fetch_wsb_mentions", lambda *a, **k: (_idx(), None))
    monkeypatch.setattr(apewisdom, "read_cached_boards", _boom)
    for novelty in (None, {}, {"enabled": False}):
        sig = WsbHypeSignal(min_mentions=50, min_mention_delta_pct=0.5, novelty=novelty)
        ems = sig.scan(date(2026, 8, 8))
        assert all(e.signal == "wsb:hype" for e in ems)
