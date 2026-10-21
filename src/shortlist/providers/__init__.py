from .base import Provider
from .mock import MockProvider

__all__ = ["Provider", "MockProvider", "build_providers"]

# Registry of synchronous `Provider`s. The live fetching providers were retired
# with the screener engine (the async harness `Source`s in `data/sources/` are
# the production data layer now). What remains is `mock`, a lightweight offline
# `StockMetrics` factory used by the scoring tests. Modules are imported lazily.
_REGISTRY = {
    "mock": ("shortlist.providers.mock", "MockProvider"),
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
        out.append(cls())
    return out
