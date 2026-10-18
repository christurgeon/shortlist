"""Option 3: local static embeddings (model2vec potion-base-8M; numpy) in the same merge pass
as opt_lex.mp*: v7 seed clusters, then a cluster joins the earliest open cluster within the
window whose ORIGINAL centroid embedding is cosine >= THR to its own.

Needs `uv run --no-project --with model2vec` (not a repo dependency)."""

import base

_MODEL = None


def _model():
    global _MODEL
    if _MODEL is None:
        from model2vec import StaticModel
        _MODEL = StaticModel.from_pretrained("minishlab/potion-base-8M")
    return _MODEL


def _merge(members, thr):
    import numpy as np
    cls = base.seed_clusters(members)
    if not members:
        return cls
    texts = [m["headline"] + ". " + m["summary"][:200] for m in members]
    E = _model().encode(texts)
    E = E / (np.linalg.norm(E, axis=1, keepdims=True) + 1e-9)
    row = {id(m): i for i, m in enumerate(members)}
    cents = []
    for ms in cls:
        c = E[[row[id(m)] for m in ms]].mean(axis=0)
        cents.append(c / (np.linalg.norm(c) + 1e-9))
    firsts = [min(m["ts"] for m in ms) for ms in cls]
    target = list(range(len(cls)))
    for j in range(len(cls)):
        for i in range(j):
            if target[i] == i and abs(firsts[j] - firsts[i]) <= base.CLUSTER_SECONDS \
                    and float(cents[i] @ cents[j]) >= thr:
                target[j] = i
                break
    out = {}
    for j, ms in enumerate(cls):
        out.setdefault(target[j], []).extend(ms)
    return list(out.values())


class _V:
    def __init__(self, thr):
        self.thr = thr

    def clusters(self, articles, ticker, name):
        return _merge(base.prefilter(articles, ticker, name), self.thr)

    def select(self, articles, ticker, name, k=10):
        return base.finish(self.clusters(articles, ticker, name), k), {}


e60, e70, e80 = _V(0.60), _V(0.70), _V(0.80)
