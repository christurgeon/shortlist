"""Convention tripwire: every *_live*.py test module must carry pytest.mark.live.

pyproject's ``addopts = "-m 'not live'"`` is the only thing keeping the default
``uv run pytest`` hermetic — a live test guarded solely by an env-var skipif
(e.g. SEC_IDENTITY) silently goes live the moment that variable is exported.
This meta-test makes the marker mandatory so hermeticity can't erode by
adjacency (the glossary AST-scan pattern).
"""
from pathlib import Path

TESTS_DIR = Path(__file__).parent


def test_every_live_test_module_declares_the_live_marker():
    offenders = []
    for path in sorted(TESTS_DIR.rglob("*_live*.py")):
        text = path.read_text()
        if "pytest.mark.live" not in text:
            offenders.append(str(path.relative_to(TESTS_DIR)))
    assert not offenders, (
        "live-network test modules missing pytest.mark.live "
        f"(default runs would hit real APIs if their env guard is set): {offenders}"
    )
