"""Shared guarded config.yaml loader for CLI entrypoints.

One load-shape contract (unreadable file, invalid YAML, empty file, non-mapping
top level, missing required keys) so every entrypoint fails the same way —
deliberately NOT schema validation. Callers catch :class:`ConfigError`, print it
(with their own program prefix) to stderr, and exit 2.
"""
from __future__ import annotations

import contextlib
from collections.abc import Iterable
from pathlib import Path

import yaml


class ConfigError(Exception):
    """A config file failed a load-shape check. The message is operator-ready:
    it names the file and the failure; print it and exit 2."""


class _NoDuplicateKeysLoader(yaml.SafeLoader):
    """Same as SafeLoader, but any mapping node with a duplicate key raises instead
    of silently keeping only the last value. The hazard is not limited to the top
    level — CLAUDE.md documents a duplicated `value:` block inside one flag/gate as
    the actual incident this guards against."""


def _construct_mapping_rejecting_duplicates(loader: yaml.SafeLoader, node, deep=False):
    seen: set = set()
    for key_node, _ in node.value:
        key = loader.construct_object(key_node, deep=deep)
        try:
            is_dup = key in seen
        except TypeError:
            is_dup = False   # unhashable key: let the real constructor raise its own error
        if is_dup:
            raise yaml.constructor.ConstructorError(
                None, None, f"duplicate key: {key!r}", key_node.start_mark)
        with contextlib.suppress(TypeError):
            seen.add(key)
    return yaml.SafeLoader.construct_mapping(loader, node, deep=deep)


_NoDuplicateKeysLoader.add_constructor(
    yaml.resolver.BaseResolver.DEFAULT_MAPPING_TAG, _construct_mapping_rejecting_duplicates)


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
        config = yaml.load(raw, Loader=_NoDuplicateKeysLoader)
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
