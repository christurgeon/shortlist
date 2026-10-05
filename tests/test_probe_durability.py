"""docs/audits/scripts/probe_durability.py — the order guards, and what every output records.

The script is the experiment's procedure: which step may run, on which data and on which code.
Each test runs its real functions inside a throwaway git repository, with no network."""
import importlib.util
import json
import random
import subprocess
import zipfile
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
SCRIPT = ROOT / "docs/audits/scripts/probe_durability.py"
SICS = ("3571", "3559", "2834", "2851", "7372", "7389", "2911", "3572")
EXITS_2019 = "0000001007"            # a synthetic filer whose last fiscal year is 2019


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
    """Seventeen December fiscal years, each filed the next February. ROIC is persistent, so the
    instrument gate has something to find."""
    quality = rng.uniform(0.02, 0.60)
    equity = rng.uniform(5e8, 5e9)
    last = 2019 if i % 15 == 7 else 2024
    tags: dict[str, list] = {t: [] for t in ("Revenues", "OperatingIncomeLoss", "GrossProfit",
                                             "StockholdersEquity", "Assets", "LongTermDebt")}
    for y in range(2008, last + 1):
        equity *= rng.uniform(0.98, 1.15)
        debt = 0.3 * equity if i % 2 else 0.0
        revenue = equity * rng.uniform(0.8, 2.0)
        dur = {"start": f"{y}-01-01", "end": f"{y}-12-31", "filed": f"{y + 1}-02-20", "form": "10-K"}
        inst = {"end": f"{y}-12-31", "filed": f"{y + 1}-02-20", "form": "10-K"}
        tags["Revenues"].append({**dur, "val": revenue})
        tags["OperatingIncomeLoss"].append(
            {**dur, "val": quality * rng.uniform(0.8, 1.2) * (equity + debt) / 0.79})
        tags["GrossProfit"].append({**dur, "val": revenue * rng.uniform(0.2, 0.7)})
        tags["StockholdersEquity"].append({**inst, "val": equity})
        tags["Assets"].append({**inst, "val": 2.5 * equity})
        if debt:
            tags["LongTermDebt"].append({**inst, "val": debt})
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
            for name, text in (extra_facts or {}).items():
                if "companyfacts" in url:
                    z.writestr(name, text)
    return download


def _read(mod, name: str) -> dict:
    return json.loads((mod.RAW / name).read_text())


# ---------------------------------------------------------------- provenance

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
    probe._write("x.json", {"k": 1})
    first = _read(probe, "x.json")
    assert first["code_commit"] == _git("rev-parse", "HEAD") and first["k"] == 1
    assert {len(first[k]) for k in ("code_sha256", "compacted_sha256", "sic_sha256")} == {64}
    Path(probe.CODE[1]).write_text("# a changed bar\n")
    _commit()
    probe._write("x.json", {"k": 1})
    second = _read(probe, "x.json")
    assert second["code_sha256"] != first["code_sha256"]
    assert second["code_commit"] != first["code_commit"]


@pytest.mark.parametrize("step", ["fetch", "gates", "discovery", "holdout"])
def test_no_step_runs_on_uncommitted_code(probe, monkeypatch, step):
    monkeypatch.setattr(probe, "_download", lambda url, dest: pytest.fail("downloaded"))
    Path(probe.CODE[2]).write_text("# an uncommitted edit\n")
    with pytest.raises(SystemExit, match="uncommitted"):
        getattr(probe, step)()


# ---------------------------------------------------------------- fetch

def test_fetch_keeps_the_archive_and_names_the_members_it_could_not_read(probe, monkeypatch):
    monkeypatch.setattr(probe, "_download", _fake_download(
        20, {"CIK0000009999.json": "{not json", "CIK0000008888.json": json.dumps({"facts": {}})}))
    probe.fetch()
    got = _read(probe, "fetch.json")
    assert (got["filers"], got["sic_found"]) == (20, 19)
    assert got["unreadable_members"] == ["CIK0000009999.json"]
    assert len(json.loads((probe.RAW / "sic.json").read_text())) == 19
    # A failed gate is diagnosed against the archive, so it outlives `fetch`. The submissions
    # archive gave only the SIC map and is gone.
    assert sorted(f.name for f in probe.CACHE.iterdir()) == ["companyfacts-10k.jsonl.gz",
                                                             "companyfacts.zip"]


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

def test_a_gates_run_that_dies_leaves_no_older_pass_behind(probe, monkeypatch):
    monkeypatch.setattr(probe, "_download", _fake_download(20))
    probe.fetch()
    (probe.RAW / "gates.json").write_text(json.dumps({"passed": True}))
    (probe.RAW / "sic.json").unlink()
    with pytest.raises(FileNotFoundError):
        probe.gates()
    assert not (probe.RAW / "gates.json").exists()


def test_the_steps_run_only_in_order_on_one_data_file_and_one_code_state(probe, monkeypatch):
    ds = probe.ds
    real_measure, real_repro = ds.measure, ds.reproduction_failures
    monkeypatch.setattr(probe, "_download", _fake_download())
    monkeypatch.setattr(probe, "DEAD_FILERS", {EXITS_2019: ("a dead filer", 2019)})
    monkeypatch.setattr(ds, "measure",
                        lambda rows, pred, outcome, **kw: real_measure(rows, pred, outcome,
                                                                       **{**kw, "reps": 25}))
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
    with pytest.raises(SystemExit, match="did not pass"):
        probe.discovery()
    assert (probe.CACHE / "companyfacts.zip").exists()

    # 3. With targets this archive can meet, the gates pass and the archive is released.
    sizes = {int(y): n for y, n in failed["universe_sizes"].items()}
    monkeypatch.setattr(ds, "reproduction_failures", lambda got: real_repro(got, sizes))
    probe.gates()
    assert _read(probe, "gates.json")["passed"] is True
    assert not (probe.CACHE / "companyfacts.zip").exists()

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
                "exit_rate_by_tercile", "beta_by_year"} <= set(m)
        assert "error" in m or {"beta", "se", "raw_tercile_spread", "bound_held", "bound_not",
                                "rank_beta", "beta_sic3_cells"} <= set(m)
    assert any("error" not in m for m in disc["tests"].values())

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
    for key, m in hold["tests"].items():
        assert isinstance(m["holdout_rule"], bool) and "new_firms_only" in m
        assert m["passes"] == (m["holdout_rule"] and all(disc["tests"][key]["rules"].values()))
    assert hold["passed"] == sorted(k for k, m in hold["tests"].items() if m["passes"])
    assert hold["code_sha256"] == disc["code_sha256"] == _read(probe, "gates.json")["code_sha256"]
