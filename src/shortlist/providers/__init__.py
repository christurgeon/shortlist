from .base import Provider
from .mock import MockProvider

__all__ = ["Provider", "MockProvider", "build_providers"]

# Registry of optional providers. Real ones import their SDK/requests lazily so
# the package works with only the mock provider installed.
_REGISTRY = {
    "mock": ("shortlist.providers.mock", "MockProvider"),
    "fmp": ("shortlist.providers.fmp", "FMPProvider"),
    "finnhub": ("shortlist.providers.finnhub", "FinnhubProvider"),
    "edgar": ("shortlist.providers.edgar", "EdgarProvider"),
    "quiver": ("shortlist.providers.extensions", "QuiverProvider"),
    "fred": ("shortlist.providers.extensions", "FredProvider"),
}


def build_providers(names: list[str], config: dict | None = None) -> list[Provider]:
    import importlib

    config = config or {}
    out: list[Provider] = []
    for n in names:
        if n not in _REGISTRY:
            raise ValueError(f"unknown provider '{n}'. Known: {list(_REGISTRY)}")
        mod_path, cls_name = _REGISTRY[n]
        cls = getattr(importlib.import_module(mod_path), cls_name)
        out.append(_construct(n, cls, config))
    return out


def _construct(name: str, cls: type, config: dict) -> Provider:
    """Instantiate a provider, passing through the config knobs it accepts. Only fmp
    is config-aware today (insider opt-in, 429 retry budget); the rest take no args."""
    if name == "fmp":
        fmp_cfg = config.get("fmp") or {}
        return cls(
            fetch_insider=fmp_cfg.get("fetch_insider", False),
            max_retries=fmp_cfg.get("max_retries", 2),
        )
    return cls()
