"""Live checks of the ROIC-persistence section against sec.gov. Skipped unless `-m live`.

    SEC_IDENTITY=you@example.com uv run pytest -m live tests/test_durability_live.py -s

WHAT ONLY A LIVE CHECK CAN SHOW: that the per-CIK company-facts API returns the same rows, in
the same order, as the bulk archive the study was compacted from. Row order matters: when two
rows for one period end have the same filed date, the first one wins (`annual_series`).

The comparison cuts the live rows to those filed on or before the day of the study's download.
It can still fail for a real reason later: the SEC can delete or re-date a fact."""
import json
import os
from datetime import date
from pathlib import Path
from types import SimpleNamespace

import pytest

from shortlist import durability, durability_profile
from shortlist.backtest.durability_data import iter_compacted
from shortlist.config import load_config
from shortlist.env import load_env
from shortlist.research import durability as rd

pytestmark = pytest.mark.live

ROOT = Path(__file__).resolve().parents[1]
COMMITTED = ROOT / "docs/audits/raw-2026-10-04-durability/companyfacts-10k.jsonl.gz"
DOWNLOADED = "2026-10-07"
CIKS = {320193: "Apple", 34088: "Exxon Mobil", 789019: "Microsoft"}


@pytest.fixture(scope="module")
def identity():
    load_env()
    value = os.environ.get("SEC_IDENTITY")
    if not value:
        pytest.skip("SEC_IDENTITY is not set")
    return value


def _cut(record: dict, last_filed: str) -> dict:
    gaap = {tag: {"units": {"USD": [r for r in node["units"]["USD"] if r["filed"] <= last_filed]}}
            for tag, node in record["facts"]["us-gaap"].items()}
    return {"facts": {"us-gaap": {t: n for t, n in gaap.items() if n["units"]["USD"]}}}


def test_the_live_api_gives_the_rows_the_study_read(identity):
    committed = {int(rec["cik"]): rec for rec in iter_compacted(COMMITTED) if int(rec["cik"]) in CIKS}
    assert set(committed) == set(CIKS)
    for cik, name in CIKS.items():
        body = json.loads(rd._download(cik, identity, 30.0))
        live = _cut(durability.compact_facts(body), DOWNLOADED)
        study = _cut(committed[cik], DOWNLOADED)
        assert live["facts"]["us-gaap"] == study["facts"]["us-gaap"], f"{name}: rows or their order differ"
        for year in range(2011, 2026):
            assert durability.snapshot(live, year) == durability.snapshot(study, year), (name, year)
        print(f"{name} (CIK {cik}): {sum(len(n['units']['USD']) for n in live['facts']['us-gaap'].values())} "
              "rows identical, in order, to the study's file")


@pytest.mark.parametrize("ticker, sic", [("MSFT", "7372"), ("XOM", "2911"), ("JPM", "6021")])
def test_the_section_for_a_real_company(identity, tmp_path, ticker, sic):
    config = load_config(str(ROOT / "config.yaml"))
    config["research"]["durability"] = {**config["research"]["durability"], "enabled": True,
                                        "cache_dir": str(tmp_path)}
    card = SimpleNamespace(ticker=ticker, metrics=SimpleNamespace(sic=sic))
    text, status = rd.fetch_section(card, SimpleNamespace(), config, today=date.today())
    print(f"\n--- {ticker}: {status}\n{text}")
    assert status == durability_profile.SHOWN or status in durability_profile.NOT_SHOWN
    assert status != durability_profile.UNAVAILABLE
