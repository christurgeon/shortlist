"""Bind KNOWN_GATES/KNOWN_FLAGS to the emission-site literals via AST scan,
so a new gate/flag literal fails CI until declared (and, via the glossary
completeness test in tests/scout/test_glossary.py, documented in /explain)."""
import ast
import inspect

import shortlist.scoring as scoring

_EMITTERS = {"check_gates", "check_flags", "score"}
_EMIT_LISTS = {"tripped", "out", "flags"}


def _appended_literals() -> set[str]:
    tree = ast.parse(inspect.getsource(scoring))
    names: set[str] = set()
    for fn in ast.walk(tree):
        if not (isinstance(fn, ast.FunctionDef) and fn.name in _EMITTERS):
            continue
        for node in ast.walk(fn):
            if (isinstance(node, ast.Call)
                    and isinstance(node.func, ast.Attribute)
                    and node.func.attr == "append"
                    and isinstance(node.func.value, ast.Name)
                    and node.func.value.id in _EMIT_LISTS
                    and node.args
                    and isinstance(node.args[0], ast.Constant)
                    and isinstance(node.args[0].value, str)):
                names.add(node.args[0].value)
    return names


def test_emitted_literals_are_declared():
    lits = _appended_literals()
    # floor guards vacuity: if the emitters were renamed/refactored the scan
    # would return {} and the subset assert would pass while checking nothing
    assert len(lits) >= 14
    assert lits <= scoring.KNOWN_GATES | scoring.KNOWN_FLAGS


def test_declared_sets_are_complete_and_disjoint():
    assert len(scoring.KNOWN_GATES) == 4
    assert len(scoring.KNOWN_FLAGS) == 20
    assert not scoring.KNOWN_GATES & scoring.KNOWN_FLAGS
    assert set(scoring._FILING_STREAM_FLAGS) <= scoring.KNOWN_FLAGS


def test_theme_descriptions_bound_to_declared_sets():
    # theme.py's report-legend maps enumerate the same 24 names; bind them
    # into the same tripwire so a new flag can't go green with a stale legend
    from shortlist.bot.report import theme
    assert set(theme.GATE_DESCRIPTIONS) == scoring.KNOWN_GATES
    assert set(theme.FLAG_DESCRIPTIONS) == scoring.KNOWN_FLAGS
