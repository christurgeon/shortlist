"""Which forms of a filing's sec.gov index URL answer? Seven free requests, one per second.

    SEC_IDENTITY=you@example.com uv run python docs/audits/scripts/probe_sec_filing_index.py

Evidence for docs/audits/2026-10-10-sec-filing-index-403.md: sec.gov denies the short index
URL that edgartools < 5.61 builds, and serves the long one."""
import os
import re
import time

import httpx

from shortlist.env import load_env

ARCHIVE = "https://www.sec.gov/Archives/edgar/data"
# (label, CIK, accession): the latest 10-K of each on 2026-10-10.
FILINGS = (("MSFT", 789019, "0001193125-26-323660"), ("JPM", 19617, "0001628280-26-008131"),
           ("AAPL", 320193, "0000320193-25-000079"))


def main() -> None:
    load_env()
    identity = os.environ.get("SEC_IDENTITY")
    if not identity:
        raise SystemExit("SEC_IDENTITY is not set")
    label, cik, acc = FILINGS[0]
    folder = f"{ARCHIVE}/{cik}/{acc.replace('-', '')}"
    urls = [(f"{name} short index", f"{ARCHIVE}/{c}/{a}-index.html") for name, c, a in FILINGS]
    urls += [(f"{label} long index", f"{folder}/{acc}-index.html"),
             (f"{label} folder", f"{folder}/"),
             (f"{label} index.json", f"{folder}/index.json"),
             (f"{label} full submission", f"{folder}/{acc}.txt")]
    with httpx.Client(headers={"User-Agent": identity, "Accept-Encoding": "gzip, deflate"},
                      timeout=60, follow_redirects=True) as client:
        for what, url in urls:
            r = client.get(url)
            title = re.search(r"<title>(.*?)</title>", r.text[:2000], re.S | re.I)
            print(f"{r.status_code}  {len(r.content):>9}  {what:<22} {url.replace(ARCHIVE, '')}"
                  f"  {title.group(1).strip()[:40] if title else ''}")
            time.sleep(1)


if __name__ == "__main__":
    main()
