"""mp20 (TF-IDF centroid merge pass) followed by a once-per-window family merge
(results, layoff only; no keyed families)."""
import base
import opt_lex
import opt_sig


class _V:
    def __init__(self, thr=0.20, fams=("results", "layoff")):
        self.thr, self.fams = thr, set(fams)

    def clusters(self, articles, ticker, name):
        ms = base.prefilter(articles, ticker, name)
        cls = opt_lex._merge_pass(ms, base.alias_words(ticker, name), self.thr)
        saved = opt_sig._FAM
        opt_sig._FAM = {k: v for k, v in saved.items() if k in self.fams}
        try:
            return opt_sig.merge(cls, keyless=self.fams)
        finally:
            opt_sig._FAM = saved

    def select(self, articles, ticker, name, k=10):
        return base.finish(self.clusters(articles, ticker, name), k), {}


rl = _V()
r = _V(fams=("results",))
l = _V(fams=("layoff",))
