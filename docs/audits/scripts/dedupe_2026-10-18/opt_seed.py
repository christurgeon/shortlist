"""Option 5, status quo: v7 seed clustering."""
import base


def clusters(articles, ticker, name):
    return base.seed_clusters(base.prefilter(articles, ticker, name))


def select(articles, ticker, name, k=10):
    return base.finish(clusters(articles, ticker, name), k), {}
