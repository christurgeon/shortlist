
from shortlist.bot.report.sections import Detail, _DeepBlock


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



