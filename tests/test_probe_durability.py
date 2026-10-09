"""docs/audits/scripts/probe_durability.py — the order guards, and what every output records.

The script is the experiment's procedure: which step may run, on which data and on which code.
Each test runs its real functions inside a throwaway git repository, with no network."""
import ast
import importlib.util
import json
import random
import subprocess
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "docs/audits/scripts/probe_durability.py"
SICS = ("3571", "3559", "2834", "6021", "7372", "7389", "2911", "6311")   # two are masked
EXITS_2019 = "0000001007"            # a synthetic filer whose last fiscal year is 2019
# Imported by a CODE file and left out of the digest on purpose: none of them decides a number.
NOT_HASHED = {"src/shortlist/models.py",                  # the StockMetrics container
              "src/shortlist/env.py",                     # .env loading, secret redaction
              "src/shortlist/edgar/sec_throttle.py"}      # the sec.gov request budget


def _git(*args: str) -> str:
    return subprocess.run(
        ["git", "-c", "user.name=t", "-c", "user.email=t@example.com", "-c", "commit.gpgsign=false",
         *args], check=True, capture_output=True, text=True).stdout.strip()


def _commit() -> None:
    _git("add", "-A")
    _git("commit", "-q", "-m", "x")


@pytest.fixture
def probe(tmp_path, monkeypatch):
    """The script, loaded fresh, with the working directory in a new repository that holds the
    pre-registration note, the real config.yaml and a stand-in for every code file."""
    spec = importlib.util.spec_from_file_location("probe_durability", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    monkeypatch.chdir(tmp_path)
    _git("init", "-q")
    Path(".gitignore").write_text(".cache/\n")
    for rel in mod.CODE:
        Path(rel).parent.mkdir(parents=True, exist_ok=True)
        Path(rel).write_text((ROOT / rel).read_text() if rel == "config.yaml" else f"# {rel}\n")
    mod.PREREG.parent.mkdir(parents=True, exist_ok=True)
    mod.PREREG.write_text("# pre-registration\n")
    _commit()
    return mod


# ---------------------------------------------------------------- a synthetic SEC archive

def _filer(i: int, rng: random.Random) -> dict:
    """Seventeen December fiscal years, each filed the next February; one filer in ten files in
    September, after the 120-day line. ROIC is persistent, so the instrument gate has something
    to find."""
    quality = rng.uniform(0.02, 0.60)
    equity = rng.uniform(5e8, 5e9)
    last = 2019 if i % 15 == 7 else 2024
    filed = "09-01" if i % 10 == 4 else "02-20"
    tags: dict[str, list] = {t: [] for t in ("Revenues", "OperatingIncomeLoss", "GrossProfit",
                                             "StockholdersEquity", "Assets", "LongTermDebt")}
    for y in range(2008, last + 1):
        equity *= rng.uniform(0.98, 1.15)
        debt = 0.3 * equity if i % 2 else 0.0
        revenue = equity * rng.uniform(0.8, 2.0)
        dur = {"start": f"{y}-01-01", "end": f"{y}-12-31", "filed": f"{y + 1}-{filed}", "form": "10-K"}
        inst = {"end": f"{y}-12-31", "filed": f"{y + 1}-{filed}", "form": "10-K"}
        if i % 13 != 5:                                           # one in thirteen: another tag
            tags["Revenues"].append({**dur, "val": revenue})
        tags["OperatingIncomeLoss"].append(
            {**dur, "val": quality * rng.uniform(0.8, 1.2) * (equity + debt) / 0.79})
        tags["GrossProfit"].append({**dur, "val": revenue * rng.uniform(0.2, 0.7)})
        tags["StockholdersEquity"].append({**inst, "val": equity})
        tags["Assets"].append({**inst, "val": 2.5 * equity})
        if debt:
            tags["LongTermDebt"].append({**inst, "val": debt})
    if i == 0:                         # one period typed with the wrong year, never to be read
        tags["OperatingIncomeLoss"].append({"start": "2105-01-01", "end": "2105-12-31", "val": 1.0,
                                            "filed": "2012-02-20", "form": "10-K"})
    return {"entityName": f"FILER {i}",
            "facts": {"us-gaap": {t: {"units": {"USD": rows}} for t, rows in tags.items() if rows}}}


def _fake_download(n: int = 160, extra_facts: dict | None = None):
    """Stands in for `_download`: writes the two archives the SEC would have sent."""
    def download(url: str, dest: Path) -> None:
        rng = random.Random(4)
        dest.parent.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(dest, "w") as z:
            for i in range(n):
                cik = f"{1000 + i:010d}"
                if "companyfacts" in url:
                    z.writestr(f"CIK{cik}.json", json.dumps(_filer(i, rng)))
                elif i % 20 != 3:                                  # one filer in twenty has no SIC
                    z.writestr(f"CIK{cik}.json", json.dumps({"sic": SICS[i % len(SICS)]}))
            for k in range(n // 16):                                # filers on form 20-F only
                if "companyfacts" in url:
                    foreign = json.dumps(_filer(n + k, rng)).replace('"10-K"', '"20-F"')
                    z.writestr(f"CIK{5000 + k:010d}.json", foreign)
            for name, text in (extra_facts or {}).items():
                if "companyfacts" in url:
                    z.writestr(name, text)
    return download


def _read(mod, name: str) -> dict:
    return json.loads((mod.RAW / name).read_text())


# ---------------------------------------------------------------- provenance

def _package_imports(rel: str) -> set[str]:
    """The `shortlist` modules a file imports, as paths from the repo root."""
    path = ROOT / rel
    here = path.relative_to(ROOT / "src").with_suffix("").parts if rel.startswith("src/") else ()
    found = set()
    for node in ast.walk(ast.parse(path.read_text())):
        if not isinstance(node, ast.ImportFrom):
            continue
        if node.level:
            base = here[:len(here) - node.level] + tuple((node.module or "").split("."))
        elif (node.module or "").split(".")[0] == "shortlist":
            base = tuple(node.module.split("."))
        else:
            continue
        base = tuple(part for part in base if part)
        for name in [None, *(alias.name for alias in node.names)]:
            mod = ROOT / "src" / Path(*base, *([name] if name else [])).with_suffix(".py")
            if mod.exists():
                found.add(str(mod.relative_to(ROOT)))
    return found


def test_the_code_digest_covers_everything_the_study_imports():
    spec = importlib.util.spec_from_file_location("probe_durability", SCRIPT)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    imported = set().union(*(_package_imports(rel) for rel in mod.CODE if rel.endswith(".py")))
    assert imported - set(mod.CODE) - NOT_HASHED == set()
    assert "src/shortlist/stats.py" in imported           # the fiscal-year length window



def test_committed_needs_a_tracked_file_with_no_edit(probe):
    note = Path("note.md")
    note.write_text("a\n")
    assert probe._committed(note) is False                 # untracked
    _commit()
    assert probe._committed(note) is True
    note.write_text("b\n")
    assert probe._committed(note) is False                 # edited since


def test_every_output_records_the_commit_and_a_digest_of_the_code(probe):
    probe.COMPACT.parent.mkdir(parents=True)
    probe.COMPACT.write_bytes(b"data")
    probe.RAW.mkdir(parents=True)
    (probe.RAW / "sic.json").write_text("{}")
    probe._write("x.json", {"k": 1}, probe._inputs())
    first = _read(probe, "x.json")
    assert first["code_commit"] == _git("rev-parse", "HEAD") and first["k"] == 1
    assert {len(first[k]) for k in ("code_sha256", "compacted_sha256", "sic_sha256")} == {64}
    Path(probe.CODE[1]).write_text("# a changed bar\n")
    _commit()
    probe._write("x.json", {"k": 1}, probe._inputs())
    second = _read(probe, "x.json")
    assert second["code_sha256"] != first["code_sha256"]
    assert second["code_commit"] != first["code_commit"]


@pytest.mark.parametrize("step", ["fetch", "gates", "discovery", "holdout"])
def test_no_step_runs_on_uncommitted_code(probe, monkeypatch, step):
    monkeypatch.setattr(probe, "_download", lambda url, dest: pytest.fail("downloaded"))
    Path(probe.CODE[2]).write_text("# an uncommitted edit\n")
    with pytest.raises(SystemExit, match="uncommitted"):
        getattr(probe, step)()


def test_an_edit_hidden_from_git_status_is_still_uncommitted_code(probe):
    _git("update-index", "--assume-unchanged", probe.CODE[2])
    Path(probe.CODE[2]).write_text("# an edit git status will not show\n")
    assert _git("status", "--porcelain") == ""
    with pytest.raises(SystemExit, match="uncommitted"):
        probe.gates()


def test_an_output_names_the_code_the_step_started_on(probe):
    probe.COMPACT.parent.mkdir(parents=True)
    probe.COMPACT.write_bytes(b"data")
    probe.RAW.mkdir(parents=True)
    (probe.RAW / "sic.json").write_text("{}")
    started = probe._inputs()
    Path(probe.CODE[1]).write_text("# committed while the step was running\n")
    _commit()
    probe._write("x.json", {}, started)
    assert _read(probe, "x.json")["code_commit"] == started["code_commit"] != _git("rev-parse", "HEAD")


def test_the_descriptive_block_survives_an_empty_cohort(probe):
    # It is computed after the bootstrap. A division by zero there would lose the whole step.
    assert probe._descriptive([])["hold_rate"] == {"held": None, "compounded": None}


# ---------------------------------------------------------------- fetch

def test_fetch_keeps_the_archive_and_names_the_members_it_could_not_read(probe, monkeypatch):
    monkeypatch.setattr(probe, "_download", _fake_download(
        20, {"CIK0000009999.json": "{not json", "CIK0000008888.json": json.dumps({"facts": {}})}))
    probe.fetch()
    got = _read(probe, "fetch.json")
    # 20 filers on form 10-K, one of them with no SIC code. The one on form 20-F is no firm to
    # the study: it is counted apart and its SIC code is not looked up.
    assert (got["filers"], got["sic_found"], got["foreign_only_filers"]) == (20, 19, 1)
    assert got["unreadable_members"] == ["CIK0000009999.json"]
    assert len(json.loads((probe.RAW / "sic.json").read_text())) == 19
    # A failed gate is diagnosed against the archives, so both outlive `fetch`.
    assert sorted(f.name for f in probe.CACHE.iterdir()) == [
        "companyfacts-10k.jsonl.gz", "companyfacts.zip", "submissions.zip"]


def test_fetch_reads_a_kept_archive_again_and_does_not_download_it_twice(probe, monkeypatch):
    # After a failed gate the remedy can be a wider tag list. That needs the SAME archive
    # compacted again, not the data of a later day.
    monkeypatch.setattr(probe, "_download", _fake_download(20))
    probe.fetch()
    first = _read(probe, "fetch.json")["compacted_sha256"]
    urls = []

    def second(url: str, dest: Path) -> None:
        urls.append(url)
        _fake_download(20)(url, dest)

    monkeypatch.setattr(probe, "_download", second)
    sic = _read(probe, "fetch.json")["sic_sha256"]
    probe.fetch()
    assert urls == []
    again = _read(probe, "fetch.json")
    assert (again["compacted_sha256"], again["sic_sha256"]) == (first, sic)


def test_fetch_stops_when_no_member_has_the_assumed_name(probe, monkeypatch):
    def download(url: str, dest: Path) -> None:
        dest.parent.mkdir(parents=True, exist_ok=True)
        with zipfile.ZipFile(dest, "w") as z:
            z.writestr("facts/0000001000.json", json.dumps(_filer(0, random.Random(1))))
    monkeypatch.setattr(probe, "_download", download)
    with pytest.raises(SystemExit, match="compacted 0 filers"):
        probe.fetch()
    assert [f.name for f in probe.CACHE.iterdir()] == ["companyfacts.zip"]    # no empty data file


# ---------------------------------------------------------------- the order, end to end

@pytest.mark.parametrize("step", ["gates", "discovery", "holdout"])
def test_a_result_is_not_overwritten_before_it_is_committed(probe, monkeypatch, step):
    # A number that was seen stays on record: a failed gate, or a discovery table, cannot be
    # replaced by a second run with no trace of the first.
    monkeypatch.setattr(probe, "_download", _fake_download(20))
    probe.fetch()
    (probe.RAW / f"{step}.json").write_text(json.dumps({"passed": False}))
    with pytest.raises(SystemExit, match=f"{step}.json holds a result that is not committed"):
        getattr(probe, step)()
    assert _read(probe, f"{step}.json") == {"passed": False}


def test_the_steps_run_only_in_order_on_one_data_file_and_one_code_state(probe, monkeypatch):
    ds = probe.ds
    real_measure, real_repro = ds.measure, ds.reproduction_failures

    def measure(rows, pred, outcome, **kw):
        m = real_measure(rows, pred, outcome, **{**kw, "reps": 25})
        if (pred, outcome) == ("track", "held"):
            # Every number present and an error beside them: what an incomplete bootstrap
            # leaves. The numbers alone would pass every rule in both windows.
            m.update(dict.fromkeys(("beta", "bound_held", "bound_not", "rank_beta",
                                    "beta_sic3_cells"), 0.5), se=0.01,
                     error="ValueError: bootstrap: 1 of 25 replications could not be fitted")
        return m

    monkeypatch.setattr(probe, "_download", _fake_download())
    monkeypatch.setattr(probe, "DEAD_FILERS", {EXITS_2019: ("a dead filer", 2019)})
    monkeypatch.setattr(ds, "measure", measure)
    probe.fetch()

    # 1. No gates.json: discovery refuses.
    with pytest.raises(SystemExit, match="gates.json is missing"):
        probe.discovery()

    # 2. The synthetic universe is nowhere near the frames counts: the gates fail, say so, and
    #    do not unlock discovery. The archive stays for the diagnosis.
    with pytest.raises(SystemExit, match="GATES FAILED"):
        probe.gates()
    failed = _read(probe, "gates.json")
    assert failed["passed"] is False and failed["completeness_ok"] is True
    assert failed["level_slope_discovery"] > 0 and failed["gap_spikes"] == []
    assert set(failed["reproduction_failures"]) == {str(y) for y in range(2011, 2025)}
    # Three counts a year: the study's universe, the same with no sector mask, and the count
    # the reproduction gate compares. That one is every filer in the archive with a ROIC: the
    # masked sectors, the late filers and the ten on form 20-F are in it.
    study, unmasked, compared = (failed[k] for k in (
        "universe_sizes", "universe_sizes_unmasked", "comparison_counts"))
    assert all(study[y] < unmasked[y] < compared[y] for y in study)
    missing = sum(1 for i in range(170) if i % 13 == 5)             # filers with no revenue tag
    assert compared["2011"] == 170 - missing and compared["2024"] < compared["2011"]
    assert 0 < failed["universe_no_revenue_tag"]["2011"] <= missing  # the unmasked ones of those
    assert failed["universe_no_revenue_tag_assets_500m"]["2011"] <= failed[
        "universe_no_revenue_tag"]["2011"]
    assert failed["facts_filed_before_period_end"] == {"rows": 1, "filers": 1}
    assert failed["python_version"].count(".") == 2
    assert all(0.3 < v < 0.7 for v in failed["zero_debt_share"]["universe"].values())   # half
    assert all(0 <= v <= 1 for v in failed["zero_debt_share"]["discovery_cohort"].values())
    assert sorted(failed["zero_debt_share"]["discovery_cohort"]) == [str(y) for y in ds.DISCOVERY]
    with pytest.raises(SystemExit, match="did not pass"):
        probe.discovery()
    assert (probe.CACHE / "companyfacts.zip").exists() and (probe.CACHE / "submissions.zip").exists()

    # 3. With targets this archive can meet, the gates pass and the archive is released. The
    #    failed result is committed first, and the pass must be committed to count.
    sizes = {int(y): n for y, n in compared.items()}
    monkeypatch.setattr(ds, "reproduction_failures", lambda got: real_repro(got, sizes))
    _commit()
    probe.gates()
    assert _read(probe, "gates.json")["passed"] is True
    assert [f.name for f in probe.CACHE.iterdir()] == ["companyfacts-10k.jsonl.gz"]
    with pytest.raises(SystemExit, match="gates.json is not committed"):
        probe.discovery()
    _commit()

    # 4. A pass counts only for the data file and the code it was computed from.
    data = probe.COMPACT.read_bytes()
    probe.COMPACT.write_bytes(data[:-1] + bytes([data[-1] ^ 1]))
    with pytest.raises(SystemExit, match="compacted_sha256"):
        probe.discovery()
    probe.COMPACT.write_bytes(data)
    Path(probe.CODE[1]).write_text("# a bar moved after the gates\n")
    _commit()
    with pytest.raises(SystemExit, match="code_sha256"):
        probe.discovery()
    Path(probe.CODE[1]).write_text(f"# {probe.CODE[1]}\n")
    _commit()
    sic = (probe.RAW / "sic.json").read_text()
    (probe.RAW / "sic.json").write_text(sic.replace("2834", "6021"))      # a firm turns bank
    with pytest.raises(SystemExit, match="sic_sha256"):
        probe.discovery()
    (probe.RAW / "sic.json").write_text(sic)

    # 5. The pre-registration must be committed as it stands.
    probe.PREREG.write_text("# pre-registration, edited after the fact\n")
    with pytest.raises(SystemExit, match="not committed"):
        probe.discovery()
    _git("checkout", "--", str(probe.PREREG))

    # 6. Discovery: eleven tests, each with the five-rule inputs and its own verdict.
    probe.discovery()
    disc = _read(probe, "discovery.json")
    assert sorted(disc["tests"]) == sorted(f"{p}/{o}" for p, o in ds.TESTS) and disc["rows"] > 100
    for m in disc["tests"].values():
        assert set(m["rules"]) == {"magnitude", "bounds", "continuous_sign", "sub_industry"}
        assert {"n", "n_no_sic", "n_no_sic_exit", "n_alone_in_cell", "n_alone_in_sic3_cell",
                "exit_rate_by_tercile", "gap_rate_by_tercile", "low_ic_rate_by_tercile",
                "beta_by_year"} <= set(m)
        assert "error" in m or {"beta", "se", "raw_tercile_spread", "bound_held", "bound_not",
                                "rank_beta", "beta_sic3_cells"} <= set(m)
    assert any("error" not in m for m in disc["tests"].values())
    errored = disc["tests"]["track/held"]
    assert errored["beta"] == 0.5 and not any(errored["rules"].values())
    assert errored["n_by_year"]["2011"] == 0 and errored["n_by_year"]["2012"] > 0
    assert sum(errored["n_by_year"].values()) == errored["n"]
    assert disc["tests"]["track/held"]["n"] < disc["tests"]["gross_margin/held"]["n"]   # no 2011

    # 7. The holdout opens only when discovery.json AND the discovery section of the verdict
    #    note are committed.
    with pytest.raises(SystemExit, match="discovery.json is not committed"):
        probe.holdout()
    _commit()
    with pytest.raises(SystemExit, match="verdict"):
        probe.holdout()                                    # no verdict note at all
    probe.VERDICT.write_text("# verdict\n\n## Gates\n\npassed\n")
    _commit()
    with pytest.raises(SystemExit, match="verdict"):
        probe.holdout()                                    # committed, with no discovery section
    probe.VERDICT.write_text("# verdict\n\n## Discovery (start years 2011-2017)\n\nnone\n")
    with pytest.raises(SystemExit, match="verdict"):
        probe.holdout()                                    # written, not committed
    _commit()

    # 8. ... and only on the data discovery ran on.
    probe.COMPACT.write_bytes(data[:-1] + bytes([data[-1] ^ 1]))
    with pytest.raises(SystemExit, match="compacted_sha256"):
        probe.holdout()
    probe.COMPACT.write_bytes(data)

    probe.holdout()
    hold = _read(probe, "holdout.json")
    assert list(hold["tests"]) == list(disc["tests"])
    assert hold["window"] == [2018, 2021] and disc["window"] == [2011, 2017]
    assert set(hold["tests"]["gross_margin/held"]["n_by_year"]) == {"2018", "2019", "2020", "2021"}
    assert set(disc["tests"]["gross_margin/held"]["n_by_year"]) == {str(y) for y in range(2011, 2018)}
    errored = hold["tests"]["track/held"]
    assert errored["beta"] == 0.5 and errored["holdout_rule"] is False
    for key, m in hold["tests"].items():
        assert isinstance(m["holdout_rule"], bool) and "new_firms_only" in m
        assert m["passes"] == (m["holdout_rule"] and all(disc["tests"][key]["rules"].values()))
    assert hold["passed"] == sorted(k for k, m in hold["tests"].items() if m["passes"])
    assert hold["code_sha256"] == disc["code_sha256"] == _read(probe, "gates.json")["code_sha256"]
