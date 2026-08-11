"""Every sec.gov request must be ATTRIBUTABLE in `RunManifest.sec_requests`.

`sec_throttle()` counts an unlabelled call as `unattributed` so no request can vanish from
the budget — but `unattributed` is a fallback, not a destination. The first production
measurement (2026-08-06) showed 10 of 756 requests landing there, i.e. 1.3% of the budget
invisible in the artifact the whole discovery workstream is being sized from.

This is an AST scan rather than a runtime assertion because the failure is a *missing
argument at a call site*, which no amount of exercising the happy path will surface: an
unlabelled call still works, still paces correctly, and still counts. Only reading the
source catches it. Same reasoning as the `KNOWN_GATES`/glossary binding scan.
"""
from __future__ import annotations

import ast
from pathlib import Path

_SRC = Path(__file__).resolve().parents[1] / "src" / "shortlist"

# Deliberately NOT scanned: `symbology.py`'s module-local `_throttle()`. That paces
# **archive.org** (Wayback CDX snapshots), a different host with its own budget — it is not
# a sec.gov consumer and must never be given a `sec_requests` label. The scan therefore
# matches the SEC convention (`throttle(...)`, a `SecThrottle` held in a parameter, plus
# the direct `sec_throttle()(...)` form) and not the bare `_throttle` name.
_SEC_CALL_NAMES = {"throttle", "sec_throttle_call"}


def _throttle_calls_missing_a_label(tree: ast.AST, module: str) -> list[str]:
    """Find `throttle(...)` / `sec_throttle()(...)` calls made with no consumer label."""
    bad: list[str] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call):
            continue
        fn = node.func
        name = None
        if isinstance(fn, ast.Name):
            name = fn.id
        elif isinstance(fn, ast.Call):
            # `sec_throttle()("dera")` — the OUTER call is the throttle invocation.
            inner = fn.func
            if isinstance(inner, ast.Name) and inner.id == "sec_throttle":
                name = "sec_throttle_call"
        if name not in _SEC_CALL_NAMES:
            continue
        if not node.args:
            bad.append(f"{module}:{node.lineno}")
    return bad


def test_every_sec_throttle_call_site_passes_a_consumer_label():
    offenders: list[str] = []
    for path in sorted(_SRC.rglob("*.py")):
        if path.name == "sec_throttle.py":      # the implementation defines the contract
            continue
        tree = ast.parse(path.read_text(), filename=str(path))
        rel = path.relative_to(_SRC).as_posix()
        offenders.extend(_throttle_calls_missing_a_label(tree, rel))
    assert not offenders, (
        "Unlabelled sec.gov throttle call sites — these land in `unattributed` and become "
        "invisible in RunManifest.sec_requests:\n  " + "\n  ".join(offenders))
