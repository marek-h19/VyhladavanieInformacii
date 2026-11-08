"""
retry_failed.py - download plant pages whose fetch failed in an earlier run.

pfaf_crawler.py logs every fetch in data/pages.tsv. A plant with an empty `file`
column and a non-permanent status (0 = connection error/timeout, 5xx, 429 ...)
was never saved, and the crawler does not retry it on resume. This script
retries each such URL once more and appends the result to pages.tsv, so
`python extract_all.py` picks the new files up.

Usage:  python retry_failed.py
"""

import csv
import sys
import time

from pfaf_crawler import PAGES_PATH, SLEEP_SECONDS, fetch, plant_name_from_url, save_html

PERMANENT = {"403", "404", "410"}      # pages that really do not exist


def failed_plant_urls():
    with open(PAGES_PATH, encoding="utf-8", newline="") as f:
        rows = list(csv.DictReader(f, delimiter="\t"))
    saved = {r["url"] for r in rows if r["kind"] == "plant" and r["file"]}
    urls, seen = [], set()
    for r in rows:
        if (r["kind"] == "plant" and not r["file"] and r["status"] not in PERMANENT
                and r["url"] not in saved and r["url"] not in seen):
            seen.add(r["url"])
            urls.append(r["url"])
    return urls


def main(sleep=SLEEP_SECONDS):
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    urls = failed_plant_urls()
    print(f"{len(urls)} plant pages to retry")
    still_failing = []
    with open(PAGES_PATH, "a", encoding="utf-8", newline="") as f:
        log = csv.writer(f, delimiter="\t")
        for i, url in enumerate(urls, 1):
            name = plant_name_from_url(url)
            status, body = fetch(url)
            rel = save_html("plant", url, name, body) if body else ""
            log.writerow(["plant", url, rel, status, 0])
            f.flush()
            print(f"[{i}/{len(urls)}] {status} {'saved' if body else 'FAILED'}  {name}")
            if not body:
                still_failing.append((name, status))
            time.sleep(sleep)
    print(f"\nrecovered: {len(urls) - len(still_failing)}, still failing: {len(still_failing)}")
    for name, status in still_failing:
        print(f"  {status}  {name}")


if __name__ == "__main__":
    main()