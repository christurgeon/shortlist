"""Wrap any variant with base experiment flags: evaluate.py flags.<flagset>__<module>__<variant>."""
import importlib
import base


class Flagged:
    def __init__(self, inner, **flags):
        self.inner, self.flags = inner, flags

    def _with(self, fn, *a):
        old = {k: getattr(base, k) for k in self.flags}
        for k, v in self.flags.items():
            setattr(base, k, v)
        try:
            return fn(*a)
        finally:
            for k, v in old.items():
                setattr(base, k, v)

    def clusters(self, *a):
        return self._with(self.inner.clusters, *a)

    def select(self, *a):
        return self._with(self.inner.select, *a)


SETS = {"F": {"FACE_BY_SUB": True}, "S": {"SPLIT_HYPHEN": True},
        "FS": {"FACE_BY_SUB": True, "SPLIT_HYPHEN": True}}


def __getattr__(name):
    fs, mod, var = (name.split("__") + [""])[:3]
    m = importlib.import_module(mod)
    return Flagged(getattr(m, var) if var else m, **SETS[fs])
