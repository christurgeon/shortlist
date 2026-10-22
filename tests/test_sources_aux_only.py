"""`snapshot_sections` on a Source must match what its module actually writes, and a
source writing only auxiliary sections must declare them: coverage relies on the
declaration to tell a normal no-data outcome from a key-data gap."""
import ast
import inspect

import pytest

from shortlist.data.models import _AUX_DEFAULTS, TickerSnapshot
from shortlist.data.sources import _REGISTRY, aux_only_sources

_SECTIONS = {f for f in TickerSnapshot.__dataclass_fields__
             if f not in ("ticker", "as_of", "raw", "provenance", "errors")}


def _written_sections(cls) -> set[str]:
    tree = ast.parse(inspect.getsource(inspect.getmodule(cls)))
    out = set()
    for node in ast.walk(tree):
        if isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            for t in targets:
                for sub in ast.walk(t):
                    if isinstance(sub, ast.Attribute) and sub.attr in _SECTIONS:
                        out.add(sub.attr)
        if (isinstance(node, ast.Call) and getattr(node.func, "id", None) == "TickerSnapshot"):
            out.update(k.arg for k in node.keywords if k.arg in _SECTIONS)
    return out


@pytest.mark.parametrize("name", sorted(_REGISTRY))
def test_declared_sections_match_what_the_module_writes(name):
    cls = _REGISTRY[name]
    written = _written_sections(cls)
    if cls.snapshot_sections:
        assert set(cls.snapshot_sections) == written
    elif written:   # undeclared and builds its snapshot elsewhere (mock): treated as key
        assert not written <= set(_AUX_DEFAULTS), (
            f"{name} writes only auxiliary sections {sorted(written)}; declare snapshot_sections")


def test_aux_only_sources_of_the_harness_chain():
    chain = ["yahoo", "fmp", "finnhub", "edgar", "finra", "wsb", "gov_contracts", "lobbying"]
    assert aux_only_sources(chain) == frozenset({"finra", "wsb", "gov_contracts", "lobbying"})


def test_aux_only_sources_ignores_unknown_names():
    assert aux_only_sources(["nope", "mock"]) == frozenset()
