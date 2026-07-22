import ast
import inspect
from shortlist.scout import monitor as mon


def test_emitted_breach_kinds_subset_of_declared():
    """Every "kind" value in the dict literals compute_alerts builds must be declared in
    KNOWN_BREACH_KINDS — so a new breach kind can't ship undocumented. compute_alerts emits
    via a DICT LITERAL ({"kind": "8k_negative", ...}), so the scan walks ast.Dict nodes for
    the value paired with the "kind" key (NOT ast.keyword, which is call syntax only)."""
    src = inspect.getsource(mon.compute_alerts)
    tree = ast.parse(src)
    emitted = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.Dict):
            for k, v in zip(node.keys, node.values):
                if (isinstance(k, ast.Constant) and k.value == "kind"
                        and isinstance(v, ast.Constant) and isinstance(v.value, str)):
                    emitted.add(v.value)
    assert emitted, "AST scan found no kind literal — scan is vacuous, fix it"
    assert emitted <= set(mon.KNOWN_BREACH_KINDS), emitted - set(mon.KNOWN_BREACH_KINDS)


def test_every_breach_kind_documented_in_glossary():
    from shortlist.scout.glossary import lookup
    for kind in sorted(mon.KNOWN_BREACH_KINDS):
        assert lookup(kind) is not None, f"no /explain entry for breach kind {kind}"
