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


def build_providers(names: list[str]) -> list[Provider]:
    import importlib

    out: list[Provider] = []
    for n in names:
        if n not in _REGISTRY:
            raise ValueError(f"unknown provider '{n}'. Known: {list(_REGISTRY)}")
        mod_path, cls_name = _REGISTRY[n]
        cls = getattr(importlib.import_module(mod_path), cls_name)
        out.append(cls())
    return out
