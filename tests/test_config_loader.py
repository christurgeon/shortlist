"""Shared guarded config loader (`shortlist.config.load_config`): ONE load-shape
contract for every CLI entrypoint that reads config.yaml, replacing the divergent
hand-rolled copies in screen.py / backtest/cli.py and the UNGUARDED loads in
scout/daily.py / scout/bot.py (which crashed with raw tracebacks).

Deliberately NOT schema validation — just the load-shape failures: unreadable
file, invalid YAML, empty file, non-mapping top level, missing required keys.
"""
from __future__ import annotations


import pytest

from shortlist.config import ConfigError, load_config


# --- happy path ---------------------------------------------------------------

def test_load_config_returns_parsed_mapping(tmp_path):
    p = tmp_path / "config.yaml"
    p.write_text("thresholds:\n  min_market_cap: 1\nweights:\n  quality: 0.5\n")
    config = load_config(p)
    assert config == {"thresholds": {"min_market_cap": 1}, "weights": {"quality": 0.5}}


def test_load_config_accepts_str_path(tmp_path):
    p = tmp_path / "config.yaml"
    p.write_text("a: 1\n")
    assert load_config(str(p)) == {"a": 1}


def test_load_config_required_keys_present(tmp_path):
    p = tmp_path / "config.yaml"
    p.write_text("thresholds: {}\nweights: {}\n")
    config = load_config(p, required_keys=("thresholds", "weights"))
    assert config["thresholds"] == {}


# --- the five load-shape failures ----------------------------------------------

def test_missing_file_raises_config_error_naming_path(tmp_path):
    missing = tmp_path / "nope.yaml"
    with pytest.raises(ConfigError, match="cannot read") as e:
        load_config(missing)
    assert "nope.yaml" in str(e.value)


def test_invalid_yaml_raises_config_error_naming_path(tmp_path):
    p = tmp_path / "broken.yaml"
    p.write_text("thresholds: [unclosed\n")
    with pytest.raises(ConfigError, match="invalid YAML") as e:
        load_config(p)
    assert "broken.yaml" in str(e.value)


def test_empty_file_raises_config_error(tmp_path):
    p = tmp_path / "empty.yaml"
    p.write_text("")                      # yaml.safe_load -> None
    with pytest.raises(ConfigError, match="empty") as e:
        load_config(p)
    assert "empty.yaml" in str(e.value)


def test_comment_only_file_is_empty_too(tmp_path):
    p = tmp_path / "comments.yaml"
    p.write_text("# nothing but comments\n")   # also parses to None
    with pytest.raises(ConfigError, match="empty"):
        load_config(p)


def test_non_mapping_top_level_raises_config_error_with_type(tmp_path):
    p = tmp_path / "list.yaml"
    p.write_text("- just\n- a\n- list\n")
    with pytest.raises(ConfigError, match="YAML mapping") as e:
        load_config(p)
    assert "list" in str(e.value)         # names the offending type


def test_missing_required_keys_names_each_missing_key(tmp_path):
    p = tmp_path / "partial.yaml"
    p.write_text("weights: {}\n")
    with pytest.raises(ConfigError) as e:
        load_config(p, required_keys=("thresholds", "weights", "gates"))
    msg = str(e.value)
    assert "thresholds" in msg and "gates" in msg      # every missing key named
    assert "weights" not in msg.split("missing")[-1]   # present key NOT listed
    assert "partial.yaml" in msg


def test_required_key_present_but_none_is_accepted(tmp_path):
    # required_keys checks PRESENCE only ("thresholds:" with a null value is the
    # operator's problem downstream) — this is load-shape, not schema, validation.
    p = tmp_path / "config.yaml"
    p.write_text("thresholds:\n")
    assert load_config(p, required_keys=("thresholds",)) == {"thresholds": None}


def test_config_error_is_a_plain_exception():
    # Callers catch ConfigError, print, and exit 2 — it must never be a SystemExit
    # subclass (that would bypass `except Exception` shims in the scout daily loop).
    assert issubclass(ConfigError, Exception)
    assert not issubclass(ConfigError, SystemExit)


# --- previously-UNGUARDED entrypoints: scout daily + bot ------------------------
# A missing/corrupt config crashed both mains with a raw traceback; they must exit
# 2 with an operator-readable one-liner instead (the screen.py/backtest contract).

def test_scout_daily_main_missing_config_exits_2(tmp_path, capsys):
    from shortlist.scout.daily import main
    rc = main(["--config", str(tmp_path / "nope.yaml")])
    assert rc == 2
    err = capsys.readouterr().err
    assert "nope.yaml" in err
    assert "Traceback" not in err


def test_scout_daily_main_invalid_yaml_exits_2(tmp_path, capsys):
    from shortlist.scout.daily import main
    p = tmp_path / "broken.yaml"
    p.write_text("scout: [unclosed\n")
    rc = main(["--config", str(p)])
    assert rc == 2
    assert "broken.yaml" in capsys.readouterr().err


def test_bot_main_missing_config_exits_2(tmp_path, capsys):
    from shortlist.scout.bot import main
    rc = main(["--config", str(tmp_path / "nope.yaml")])
    assert rc == 2
    err = capsys.readouterr().err
    assert "nope.yaml" in err
    assert "Traceback" not in err


def test_bot_main_non_mapping_config_exits_2(tmp_path, capsys):
    from shortlist.scout.bot import main
    p = tmp_path / "list.yaml"
    p.write_text("- a\n- b\n")
    rc = main(["--config", str(p)])
    assert rc == 2
    assert "list.yaml" in capsys.readouterr().err
