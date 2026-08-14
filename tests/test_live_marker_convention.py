"""Convention tripwire: every *_live*.py test module must carry pytest.mark.live.

pyproject's ``addopts = "-m 'not live'"`` is the only thing keeping the default
``uv run pytest`` hermetic — a live test guarded solely by an env-var skipif
(e.g. SEC_IDENTITY) silently goes live the moment that variable is exported.
This meta-test makes the marker mandatory so hermeticity can't erode by
adjacency (the glossary AST-scan pattern).
"""
from pathlib import Path

TESTS_DIR = Path(__file__).parent

# Env vars whose only job is to unlock a real network call. A module that gates a
# skipif on one of these is live by definition, whatever it is named.
LIVE_CREDENTIAL_ENV = (
    "SEC_IDENTITY", "RUN_LIVE_EDGAR", "SHORTLIST_LIVE",
    "FINNHUB_API_KEY", "FMP_API_KEY", "FRED_API_KEY",
)


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


def test_env_guarded_modules_declare_the_live_marker():
    """The filename rule above is necessary but not sufficient: it misses a live test
    in a conventionally-named module. Both offenders found on 2026-08-14
    (``research/test_filings_integration.py``, ``test_edgar_source_financials.py``)
    were named like unit tests, so ``-m 'not live'`` did not exclude them and only the
    absence of the env var kept them hermetic — and ``SEC_IDENTITY`` ships in the repo
    ``.env`` behind an ``export`` prefix, so one ``source .env`` makes them live.
    """
    offenders = []
    for path in sorted(TESTS_DIR.rglob("test_*.py")):
        if path == Path(__file__):
            continue
        text = path.read_text()
        if "skipif" not in text or "pytest.mark.live" in text:
            continue
        named = sorted(e for e in LIVE_CREDENTIAL_ENV if e in text)
        if named:
            offenders.append(f"{path.relative_to(TESTS_DIR)} (gates on {', '.join(named)})")
    assert not offenders, (
        "test modules gate a skipif on a live-credential env var but declare no "
        f"pytest.mark.live, so a default run goes live once it is set: {offenders}"
    )
