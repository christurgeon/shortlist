
from shortlist.bot.report.sections import (Detail, _DeepBlock, _PriorPicks)


class _VM:
    """Minimal duck-typed view model for section unit tests."""
    def __init__(self, deep, picks):
        self.deep_block = deep
        self.prior_picks = picks


def test_deep_block_chunks_three_per_line():
    vm = _VM(["A", "B", "C", "D"], [])
    assert _DeepBlock().applies(vm)
    txt = "\n".join(_DeepBlock().render_text(vm, Detail.FULL))
    assert "/deep A, B, C" in txt
    assert "/deep D" in txt
    assert "not investment advice" in txt.lower()


def test_deep_block_absent_when_empty():
    assert not _DeepBlock().applies(_VM([], []))
    assert _DeepBlock().render_text(_VM([], []), Detail.FULL) == []


def test_prior_picks_shows_excess_and_bucket():
    picks = [{"ticker": "XYZ", "ret": 0.30, "excess": 0.20, "horizon_bucket": "3m",
              "evidence": "Activist 13D: Elliott → XYZ"}]
    vm = _VM([], picks)
    assert _PriorPicks().applies(vm)
    txt = "\n".join(_PriorPicks().render_text(vm, Detail.FULL))
    assert "XYZ" in txt and "+20" in txt and "3m" in txt


def test_prior_picks_none_safe_dash():
    picks = [{"ticker": "ABC", "ret": None, "excess": None, "horizon_bucket": None,
              "evidence": ""}]
    txt = "\n".join(_PriorPicks().render_text(_VM([], picks), Detail.FULL))
    assert "ABC" in txt and "—" in txt


# ---- byte-identical-absent pin (digest-verdicts Task 2) ----
