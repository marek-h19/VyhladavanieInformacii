"""
retry_failed.py - download plant pages whose fetch failed in an earlier run.

pfaf_crawler.py logs every fetch in data/pages.tsv. A plant with an empty `file`
column and a non-permanent status (0 = connection error/timeout, 5xx, 429 ...)
was never saved, and the crawler does not retry it on resume. This script
retries each such URL once more and appends the result to pages.tsv, so
`python extract_all.py` picks the new files up.

Usage:  python retry_failed.py                 (1 attempt, 30 s timeout, stops after 8 failures in a row)
        python retry_failed.py --timeout 60 --stop-after 0
"""

import argparse
import csv
import sys
import time

import pfaf_crawler as crawler
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


def main(argv=None):
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description="retry plant pages that failed to download")
    parser.add_argument("--timeout", type=float, default=30, help="seconds per request")
    parser.add_argument("--attempts", type=int, default=1, help="attempts per page")
    parser.add_argument("--stop-after", type=int, default=8,
                        help="stop after this many failures in a row (0 = never)")
    parser.add_argument("--sleep", type=float, default=SLEEP_SECONDS)
    args = parser.parse_args(argv)
    crawler.TIMEOUT = args.timeout                 # fetch() reads these module settings
    crawler.MAX_RETRIES = args.attempts

    urls = failed_plant_urls()
    print(f"{len(urls)} plant pages to retry (timeout {args.timeout:g} s, "
          f"{args.attempts} attempt(s) each)")
    still_failing, streak = [], 0
    with open(PAGES_PATH, "a", encoding="utf-8", newline="") as f:
        log = csv.writer(f, delimiter="\t")
        for i, url in enumerate(urls, 1):
            name = plant_name_from_url(url)
            status, body = fetch(url)
            rel = save_html("plant", url, name, body) if body else ""
            log.writerow(["plant", url, rel, status, 0])
            f.flush()
            print(f"[{i}/{len(urls)}] {status} {'saved' if body else 'FAILED'}  {name}")
            streak = 0 if body else streak + 1
            if not body:
                still_failing.append((name, status))
            if args.stop_after and streak >= args.stop_after:
                print(f"\nstopped: {streak} failures in a row - the cause is probably systematic "
                      "(blocked/slow server or network). Diagnose before retrying.")
                break
            time.sleep(args.sleep)
    print(f"\nrecovered: {i - len(still_failing) if urls else 0} of {len(urls)} planned, "
          f"failing: {len(still_failing)}")


if __name__ == "__main__":
    main()
