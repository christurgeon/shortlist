"""Every declarative flag must have a producer for the field its rule reads.

WHY: `filing_text_change` shipped wired end-to-end through scoring, config, the
glossary and the bot theme with NOTHING ever setting `filing_text_similarity`
(TODO.md §2a). The flag could never fire. This guard makes the next one visible
at CI time rather than in a code review two months later.
"""
import ast
from pathlib import Path

import pytest

SRC = Path(__file__).resolve().parents[1] / "src" / "shortlist"

# flag name -> the StockMetrics field its scoring rule reads
FLAG_INPUTS = {"filing_text_change": "filing_text_similarity"}

# Not producers: the dataclass declaration itself, the offline demo factory, and
# the presentation layer (which only forwards values it was handed).
EXCLUDED = ("models.py", "providers/mock.py", "bot/")


def _is_excluded(path: Path) -> bool:
    rel = path.relative_to(SRC).as_posix()
    return any(rel.endswith(e) or rel.startswith(e) or f"/{e}" in rel for e in EXCLUDED)


def _writes_field(tree: ast.AST, field: str) -> bool:
    """True if this module ASSIGNS `<something>.field` or passes `field=` as a
    keyword. Covers `m.roic = x` (bridge.py's dominant style), `m.roic: float = x`
    and `StockMetrics(roic=x)` alike."""
    for node in ast.walk(tree):
        targets = []
        if isinstance(node, ast.Assign):
            targets = node.targets
        elif isinstance(node, ast.AnnAssign):
            targets = [node.target]
        for tgt in targets:
            if isinstance(tgt, ast.Attribute) and tgt.attr == field:
                return True
        if isinstance(node, ast.keyword) and node.arg == field:
            return True
    return False


def _producers(field: str) -> list[str]:
    out = []
    for path in SRC.rglob("*.py"):
        if _is_excluded(path):
            continue
        try:
            tree = ast.parse(path.read_text(encoding="utf-8"))
        except SyntaxError:                      # pragma: no cover
            continue
        if _writes_field(tree, field):
            out.append(path.relative_to(SRC).as_posix())
    return out


def test_ast_producer_detection_sees_the_repo_dominant_style():
    """Self-test the detector against a field with a KNOWN producer, so a broken
    detector cannot make the guard below look meaningful."""
    assert "data/bridge.py" in _producers("roic")


@pytest.mark.xfail(
    strict=True,
    reason="filing_text_change has no producer on the screen path: the similarity is "
           "computed in the research layer, which runs AFTER check_flags "
           "(scoring.py:809 inside score(), vs screen.py:188 then :193). Tracked in "
           "TODO.md §2a. When a collection-time producer ships, this test XPASSes, "
           "strict=True turns that into a failure, and whoever added it deletes "
           "this decorator.")
def test_declared_flag_inputs_have_a_writer():
    for flag, field in FLAG_INPUTS.items():
        assert _producers(field), (
            f"flag {flag!r} reads {field!r}, but nothing in src/ ever assigns it — "
            "the flag can never fire (TODO.md §2a)")
