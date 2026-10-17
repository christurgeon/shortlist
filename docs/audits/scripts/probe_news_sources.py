"""Source landscape probe: which news feeds we can reach, and what they return.

Usage: uv run python probe_news_sources.py OUTDIR TICKER=Name [TICKER=Name ...]
Writes OUTDIR/<source>/<TICKER>.json (raw) and prints a per-source summary.
Prints no secrets: every error string goes through redact_secrets.
"""
import json, os, sys, time, collections, statistics, xml.etree.ElementTree as ET
from datetime import date, timedelta
from pathlib import Path
from urllib.parse import quote_plus
import httpx
from shortlist.env import load_env, redact_secrets

load_env()
FH, FMP = os.environ.get("FINNHUB_API_KEY"), os.environ.get("FMP_API_KEY")
UA = {"User-Agent": "shortlist-research-probe " + os.environ.get("SEC_IDENTITY", "")}
today = date.today()
frm = (today - timedelta(days=30)).isoformat()


def finnhub(t, name):
    r = httpx.get("https://finnhub.io/api/v1/company-news", params={
        "symbol": t, "from": frm, "to": today.isoformat(), "token": FH}, timeout=20)
    r.raise_for_status()
    return [{"ts": a.get("datetime"), "source": a.get("source"), "headline": a.get("headline"),
             "summary": a.get("summary"), "url": a.get("url"), "related": a.get("related")}
            for a in r.json()]


def fmp(t, name):
    r = httpx.get("https://financialmodelingprep.com/stable/news/stock", params={
        "symbols": t, "limit": 50, "apikey": FMP}, timeout=20)
    if r.status_code != 200:
        raise RuntimeError(f"HTTP {r.status_code}: {r.text[:120]}")
    return [{"ts": a.get("publishedDate"), "source": a.get("publisher") or a.get("site"),
             "headline": a.get("title"), "summary": a.get("text"), "url": a.get("url")}
            for a in r.json()]


def _rss(url):
    r = httpx.get(url, headers=UA, timeout=20, follow_redirects=True)
    r.raise_for_status()
    root = ET.fromstring(r.content)
    out = []
    for it in root.iter("item"):
        src = it.find("source")
        out.append({"ts": it.findtext("pubDate"), "source": src.text if src is not None else None,
                    "headline": it.findtext("title"), "summary": it.findtext("description"),
                    "url": it.findtext("link")})
    return out


def yahoo_rss(t, name):
    return _rss(f"https://feeds.finance.yahoo.com/rss/2.0/headline?s={t}&region=US&lang=en-US")


def google_rss(t, name):
    q = quote_plus(f'"{name}" when:30d')
    return _rss(f"https://news.google.com/rss/search?q={q}&hl=en-US&gl=US&ceid=US:en")


def gdelt(t, name):
    q = quote_plus(f'"{name}" sourcelang:english')
    r = httpx.get(f"https://api.gdeltproject.org/api/v2/doc/doc?query={q}&mode=artlist"
                  "&format=json&timespan=30d&maxrecords=75&sort=datedesc", timeout=30)
    r.raise_for_status()
    try:
        arts = r.json().get("articles", [])
    except ValueError:
        raise RuntimeError(f"non-JSON: {r.text[:120]}")
    return [{"ts": a.get("seendate"), "source": a.get("domain"), "headline": a.get("title"),
             "summary": None, "url": a.get("url")} for a in arts]


SOURCES = {"finnhub": finnhub, "fmp": fmp, "yahoo_rss": yahoo_rss,
           "google_rss": google_rss, "gdelt": gdelt}

if __name__ == "__main__":
    out = Path(sys.argv[1])
    pairs = [a.split("=", 1) for a in sys.argv[2:]]
    for sname, fn in SOURCES.items():
        (out / sname).mkdir(parents=True, exist_ok=True)
        print(f"== {sname}")
        for t, name in pairs:
            try:
                items = fn(t, name)
            except Exception as e:
                print(f"  {t}: ERROR {redact_secrets(str(e))[:160]}")
                continue
            (out / sname / f"{t}.json").write_text(json.dumps(items, indent=1))
            srcs = collections.Counter(i["source"] for i in items).most_common(5)
            summ = [len(i["summary"] or "") for i in items]
            med = statistics.median(summ) if summ else 0
            print(f"  {t}: n={len(items)} median_summary={med} top_sources={srcs}")
            time.sleep(1.2 if sname == "gdelt" else 0.3)
