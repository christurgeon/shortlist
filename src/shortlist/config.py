"""Shared guarded config.yaml loader for CLI entrypoints.

One load-shape contract (unreadable file, invalid YAML, empty file, non-mapping
top level, missing required keys) so every entrypoint fails the same way —
deliberately NOT schema validation. Callers catch :class:`ConfigError`, print it
(with their own program prefix) to stderr, and exit 2.
"""
from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path

import yaml


class ConfigError(Exception):
    """A config file failed a load-shape check. The message is operator-ready:
    it names the file and the failure; print it and exit 2."""


def load_config(path: str | Path, required_keys: Iterable[str] = ()) -> dict:
    """Read and parse a YAML config file, enforcing the load-shape contract.

    Raises ConfigError on: an unreadable/missing file, invalid YAML, an empty
    file (parses to None), a non-mapping top level, or any of `required_keys`
    absent from the top-level mapping (presence-only — a present-but-null key
    is downstream's concern).
    """
    p = Path(path)
    try:
        raw = p.read_text()
    except OSError as e:
        raise ConfigError(f"cannot read config file {p}: {e}") from e
    try:
        config = yaml.safe_load(raw)
    except yaml.YAMLError as e:
        raise ConfigError(f"invalid YAML in config file {p}: {e}") from e
    if config is None:
        raise ConfigError(f"config file {p} is empty")
    if not isinstance(config, dict):
        raise ConfigError(f"config file {p} must be a YAML mapping, "
                          f"got {type(config).__name__}")
    missing = [k for k in required_keys if k not in config]
    if missing:
        raise ConfigError(f"config file {p} is missing required "
                          f"key(s): {', '.join(missing)}")
    return config
