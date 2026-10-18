"""Events that lose visibility under an option: labelled events SHOWN (as a face) by the status
quo at K=10 but not shown by the option, and the option item that absorbed their articles.
Usage: uv run python hidden.py opt_x [...]"""
import importlib
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import evaluate as E  # noqa: E402
import opt_seed  # noqa: E402


def resolve(name):
    mod, _, var = name.partition(".")
    m = importlib.import_module(mod)
    return getattr(m, var) if var else m


def shown_events(mod, arts, t, n, single):
    items, _ = mod.select(arts, t, n, E.K)
    return {single.get(E.face_id(arts, it)): it for it in items} - {None} if False else \
        {single[E.face_id(arts, it)]: it for it in items if E.face_id(arts, it) in single}


if __name__ == "__main__":
    for name in sys.argv[1:]:
        mod = resolve(name)
        lost = gained = 0
        print("==", name)
        for t, n, arts, ev in E.labelled():
            single = {}
            for j, e in enumerate(ev):
                for i in e["ids"]:
                    single.setdefault(str(i), j)
            a = shown_events(opt_seed, arts, t, n, single)
            b = shown_events(mod, arts, t, n, single)
            items, _ = mod.select(arts, t, n, E.K)
            for j in sorted(set(a) - set(b)):
                lost += 1
                ids = {str(i) for i in ev[j]["ids"]}
                host = next((it["headline"][:70] for it in items if ids & set(it["ids"])), "(not shown)")
                print(f"  LOST {t} [{ev[j]['tier']}] {ev[j]['label'][:60]}  -> under: {host}")
            for j in sorted(set(b) - set(a)):
                gained += 1
                print(f"  GAIN {t} [{ev[j]['tier']}] {ev[j]['label'][:60]}")
        print(f"  lost {lost} gained {gained}")
