import argparse
import csv
import hashlib
import os
import re
import time
from collections import Counter
from urllib.parse import parse_qsl, quote_plus, urlsplit


import requests

from diverse_frontier import DiverseFrontier, plant_key, split_links, url_class
from extractor import FIELDNAMES, extract_row

ROOT = "https://pfaf.org"
PLANT_URL = ROOT + "/user/Plant.aspx?LatinName="
START_URLS = [ROOT + "/user/Default.aspx"]

HEADERS = {
    "User-Agent": ("VI2026-SkolskyProjekt/1.0 "
                   "(contact: xhrabkovsky@stuba.sk; purpose: university project)"),
    "Accept-Language": "en-US,en;q=0.9",
}
TIMEOUT = 10            # max time per request (seconds)
SLEEP_SECONDS = 1.5     # politeness delay between requests
MAX_RETRIES = 3
MAX_PER_CLASS = 300     # max fetches per URL template

SKIP_EXT = (".jpg", ".jpeg", ".png", ".gif", ".svg", ".ico", ".webp", ".pdf", ".zip",
            ".rar", ".css", ".js", ".mp3", ".mp4", ".xml", ".doc", ".docx", ".xls", ".xlsx")

DATA_DIR = "data"
PAGES_PATH = os.path.join(DATA_DIR, "pages.tsv")
PLANTS_PATH = os.path.join(DATA_DIR, "plants_full.tsv")
DISCOVERED_PATH = os.path.join(DATA_DIR, "discovered.txt")


# robots.txt
def load_robots() -> list:
    """Disallow prefixes that apply to 'User-agent: *' (wildcard tails stripped)."""
    try:
        text = requests.get(ROOT + "/robots.txt", headers=HEADERS, timeout=TIMEOUT).text
    except requests.RequestException:
        text = ""
    prefixes, applies, last_was_agent = [], False, False
    for raw in text.splitlines():
        line = raw.split("#")[0].strip()
        if not line or ":" not in line:
            continue
        field, value = [part.strip() for part in line.split(":", 1)]
        field = field.lower()
        if field == "user-agent":
            if not last_was_agent:
                applies = False              # a new group of rules starts here
            applies = applies or value == "*"
            last_was_agent = True
            continue
        last_was_agent = False
        if applies and field == "disallow" and value:
            prefixes.append(value.rstrip("*").lower())
    return prefixes


def is_crawlable(url, disallowed) -> bool:
    parts = urlsplit(url)
    if parts.path.lower().endswith(SKIP_EXT):
        return False
    target = (parts.path + ("?" + parts.query if parts.query else "")).lower()
    return not any(target.startswith(prefix) for prefix in disallowed)


# fetching
def fetch(url) -> tuple:
    """Return (status, html_text); html_text is '' for failures and non-HTML."""
    status = 0
    for attempt in range(1, MAX_RETRIES + 1):
        try:
            resp = requests.get(url, headers=HEADERS, timeout=TIMEOUT)
            status = resp.status_code
            if status == 200:
                is_html = "html" in resp.headers.get("Content-Type", "").lower()
                return status, (resp.text if is_html else "")
            if status in (403, 404, 410):    # permanent errors: do not retry
                return status, ""
        except requests.RequestException as err:
            print(f"  [warn] attempt {attempt}/{MAX_RETRIES} failed: {err}")
        time.sleep(2 * attempt)
    return status, ""


# returns latin name
def plant_name_from_url(url) -> str:
    query = {k.lower(): v for k, v in parse_qsl(urlsplit(url).query)}
    return re.sub(r"\s+", " ", query.get("latinname", "")).strip()


# add starting URL to frontier
def add_start(frontier, names, url):
    name = plant_name_from_url(url)
    if name:
        names.setdefault(plant_key(name), name)
        frontier.add_seed_plant(plant_key(name))
    else:
        frontier.add_seed_page(url)

# processes the downloaded page, extracts links to plants and other internal pages
# using split_links(), filters out disallowed URLs based on robots.txt, and
# subsequently passes the obtained information to the frontier, which updates the
# discovery status of new plants and URLs"
def ingest(frontier, names, kind, url, text, disallowed, key=None):
    """Feed one downloaded (or replayed) page into the frontier; returns new plants."""
    # plants and internal pages
    found, others = split_links(text, url)
    for name in found:
        names.setdefault(plant_key(name), name)
    keys = [plant_key(name) for name in found]
    others = [u for u in others if is_crawlable(u, disallowed)]
    if kind == "plant":
        return frontier.on_plant_page(key, keys, others)
    return frontier.on_hub_page(url, keys, others)


def save_html(kind, url, name, body):
    if kind == "plant":
        stem = re.sub(r"[^A-Za-z0-9_+-]", "_", name)[:120]
        stem += "_" + hashlib.sha1(name.encode("utf-8")).hexdigest()[:6]
        rel = "raw_html/" + stem + ".html"
    else:
        rel = "other_html/" + hashlib.sha1(url.encode("utf-8")).hexdigest()[:16] + ".html"
    with open(os.path.join(DATA_DIR, rel), "w", encoding="utf-8") as f:
        f.write(body)
    return rel


def replay(frontier, names, class_count, disallowed):
    """Rebuild the frontier from pages saved in earlier runs (resume support)."""
    if not os.path.exists(PAGES_PATH):
        return 0
    count = 0
    with open(PAGES_PATH, encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f, delimiter="\t"):
            text = ""
            if row["file"] and os.path.exists(os.path.join(DATA_DIR, row["file"])):
                with open(os.path.join(DATA_DIR, row["file"]), encoding="utf-8") as page:
                    text = page.read()
            key = None
            if row["kind"] == "plant":
                name = plant_name_from_url(row["url"])
                key = plant_key(name)
                names.setdefault(key, name)
            else:
                class_count[url_class(row["url"])] += 1
            ingest(frontier, names, row["kind"], row["url"], text, disallowed, key)
            count += 1
    return count


def run_crawl(start_urls, max_fetches, sleep):
    os.makedirs(os.path.join(DATA_DIR, "raw_html"), exist_ok=True)
    os.makedirs(os.path.join(DATA_DIR, "other_html"), exist_ok=True)
    disallowed = load_robots()
    print(f"robots.txt: {len(disallowed)} disallowed prefixes")

    frontier = DiverseFrontier(seed=0)
    names, class_count = {}, Counter()
    # before replay, so start pages are not refetched
    for url in start_urls:
        add_start(frontier, names, url)
    replayed = replay(frontier, names, class_count, disallowed)
    print(f"resumed from {replayed} saved pages; {frontier.report()}")

    plants_is_new = not os.path.exists(PLANTS_PATH)
    pages_is_new = not os.path.exists(PAGES_PATH)
    done = 0

    with open(PLANTS_PATH, "a", encoding="utf-8", newline="") as plants_file, \
            open(PAGES_PATH, "a", encoding="utf-8", newline="") as pages_file:
        plants_out = csv.DictWriter(plants_file, FIELDNAMES, delimiter="\t")
        pages_out = csv.writer(pages_file, delimiter="\t")
        if plants_is_new:
            plants_out.writeheader()
        if pages_is_new:
            pages_out.writerow(["kind", "url", "file", "status", "new_plants"])

        def budget_left():
            return not max_fetches or done < max_fetches

        def visit(kind, item):
            nonlocal done
            url = PLANT_URL + quote_plus(names[item]) if kind == "plant" else item
            status, body = fetch(url)
            name = names[item] if kind == "plant" else ""
            rel = save_html(kind, url, name, body) if body else ""
            new = ingest(frontier, names, kind, url, body, disallowed, key=item)
            if kind == "plant" and body:
                plants_out.writerow(extract_row(name, body))
            pages_out.writerow([kind, url, rel, status, new])
            plants_file.flush()
            pages_file.flush()
            done += 1
            print(f"[{done}] {kind:5} {status} new={new:<4} known={len(frontier.known)} {url}")
            time.sleep(sleep)

        # phase 1: discovery - the frontier picks pages expected to reveal new plants
        while budget_left():
            step = frontier.next_target()
            if step is None or frontier.stalled():
                break
            kind, item = step
            if kind == "hub" and class_count[url_class(item)] >= MAX_PER_CLASS:
                frontier.on_hub_page(item, [], [])      # skip: URL template exhausted
                continue
            if kind == "hub":
                class_count[url_class(item)] += 1
            visit(kind, item)

        # phase 2: harvest - download every discovered plant that is still missing
        while budget_left():
            todo = frontier.harvest_list()
            if not todo:
                break
            for key in todo:
                if not budget_left():
                    break
                visit("plant", key)

    with open(DISCOVERED_PATH, "w", encoding="utf-8") as f:
        for key in sorted(frontier.known):
            f.write(names.get(key, key) + "\n")
    print(f"\nDone. fetched this run: {done}")
    print(frontier.report())
    return frontier


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="pfaf.org internal-link crawler")
    parser.add_argument("--start", action="append", default=None,
                        help="start URL (repeatable); default: the database home page")
    parser.add_argument("--max-fetches", type=int, default=50,
                        help="max pages in this run, 0 = unlimited")
    parser.add_argument("--sleep", type=float, default=SLEEP_SECONDS,
                        help="seconds between requests")
    args = parser.parse_args()
    run_crawl(args.start or START_URLS, args.max_fetches, args.sleep)