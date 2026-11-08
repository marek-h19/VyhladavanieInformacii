
import html
import math
import random
import re
import sys
from collections import defaultdict, deque
from urllib.parse import parse_qsl, unquote_plus, urljoin, urlsplit

# Any "LatinName=<prefix>%" query is a systematic enumeration -> never used.
FORBIDDEN_RE = re.compile(r"databasesearhresult\.aspx\?latinname=[a-z ]*(%25|%)$", re.I)
HREF_RE = re.compile(r"""href\s*=\s*["']([^"']+)["']""", re.I)


def plant_key(name):
    """Canonical key of a plant: decoded, single-spaced, lower-case."""
    return re.sub(r"\s+", " ", unquote_plus(name)).strip().lower()


def is_forbidden(url):
    return bool(FORBIDDEN_RE.search(url))


def url_class(url):
    """URL template: first path segment + page name + sorted query keys.
    Values are dropped so that thousands of similar pages share one class."""
    parts = urlsplit(url)
    segs = [s for s in parts.path.lower().split("/") if s]
    head = segs[0] if segs else ""
    last = segs[-1] if segs and "." in segs[-1] else "*"
    keys = ",".join(sorted(k.lower() for k, _ in parse_qsl(parts.query, keep_blank_values=True)))
    return f"{head}/{last}?{keys}"


def split_links(html_text, base_url):
    """Split all hrefs of a page into (plant names, other same-site URLs).
    Plant names keep their original spelling; use plant_key() to canonicalize.
    Pure regex, no HTML parser."""
    host = urlsplit(base_url).netloc.lower()
    plants, others = [], []
    for href in HREF_RE.findall(html_text):
        href = html.unescape(href).split("#")[0].strip()   # &#215; -> x, drop fragment
        if not href or href.lower().startswith(("javascript:", "mailto:")):
            continue
        absolute = urljoin(base_url, href)
        parts = urlsplit(absolute)
        if parts.netloc.lower() != host:
            continue
        if parts.path.lower().endswith("/user/plant.aspx"):
            query = dict((k.lower(), v) for k, v in parse_qsl(parts.query))
            if query.get("latinname"):
                plants.append(re.sub(r"\s+", " ", query["latinname"]).strip())
        elif not is_forbidden(absolute):
            others.append(absolute)
    return plants, others


class DiverseFrontier:
    def __init__(self, epsilon=0.05, ucb_c=40.0, seed=None):
        self.rng = random.Random(seed)
        self.eps = epsilon
        self.ucb_c = ucb_c
        self.known = set()                    # every discovered plant key
        self.fetched = set()                  # plant pages already fetched
        self._parent = {}                     # union-find over plant keys
        self._todo = {}                       # cluster root -> unfetched keys
        self._nfetch = defaultdict(int)       # cluster root -> pages fetched
        self._ema = defaultdict(float)        # cluster root -> smoothed gain
        self._first_gains = []                # gains of first fetches in clusters
        self._hub_todo = {}                   # non-plant url -> url class
        self._hub_seen = set()
        self._cls_n = defaultdict(int)        # bandit: pulls per url class
        self._cls_sum = defaultdict(float)    # bandit: summed gain per url class
        self.recent = deque(maxlen=30)        # last gains, for stall detection
        self.bridges = 0                      # plant->plant edges leaving a cluster

    # ---------- union-find ----------
    def _find(self, k):
        parent = self._parent
        while parent[k] != k:
            parent[k] = parent[parent[k]]
            k = parent[k]
        return k

    def _union(self, a, b):
        ra, rb = self._find(a), self._find(b)
        if ra == rb:
            return ra
        if len(self._todo[ra]) < len(self._todo[rb]):
            ra, rb = rb, ra
        self._parent[rb] = ra
        self._todo[ra] |= self._todo.pop(rb)
        self._nfetch[ra] += self._nfetch.pop(rb, 0)
        self._ema[ra] = max(self._ema[ra], self._ema.pop(rb, 0.0))
        return ra

    # ---------- bookkeeping ----------
    def _add_plant(self, key):
        if key in self.known:
            return False
        self.known.add(key)
        self._parent[key] = key
        self._todo[key] = {key}
        return True

    def _register_hubs(self, urls):
        for url in urls:
            if url in self._hub_seen or is_forbidden(url):
                continue
            self._hub_seen.add(url)
            self._hub_todo[url] = url_class(url)

    def add_seed_plant(self, key):
        self._add_plant(key)

    def add_seed_page(self, url):
        self._register_hubs([url])

    # ---------- page results ----------
    def on_plant_page(self, key, plant_keys, other_urls):
        """Call after fetching a plant page. Returns the number of new plants."""
        self._add_plant(key)
        first = self._nfetch[self._find(key)] == 0
        self._todo[self._find(key)].discard(key)
        self.fetched.add(key)
        new = 0
        for other in plant_keys:
            if other == key:
                continue
            if self._add_plant(other):
                new += 1
            ro = self._find(other)
            if ro != self._find(key):
                if self._nfetch[ro] == 0:
                    self._union(key, other)     # claim an unexplored cluster
                else:
                    self.bridges += 1           # link into an explored cluster
        root = self._find(key)
        if first:
            self._first_gains.append(new)
            self._ema[root] = float(new)
        else:
            self._ema[root] = 0.5 * self._ema[root] + 0.5 * new
        self._nfetch[root] += 1
        self._register_hubs(other_urls)
        self.recent.append(new)
        return new

    def on_hub_page(self, url, plant_keys, other_urls):
        """Call after fetching a non-plant page (category list, nav page...)."""
        self._hub_todo.pop(url, None)
        new = sum(1 for key in plant_keys if self._add_plant(key))
        cls = url_class(url)
        self._cls_n[cls] += 1
        self._cls_sum[cls] += new
        self._register_hubs(other_urls)
        self.recent.append(new)
        return new

    # ---------- choosing the next page ----------
    def next_target(self):
        """Return ('plant', key) or ('hub', url) or None when nothing is left."""
        prior = 1.5 * (sum(self._first_gains) / len(self._first_gains)
                       if self._first_gains else 10.0)
        cands = []
        for root, todo in self._todo.items():
            if todo:
                score = prior if self._nfetch[root] == 0 else self._ema[root]
                cands.append((score, "cluster", root))
        total = sum(self._cls_n.values()) + 1
        seen_classes = set()
        for url, cls in self._hub_todo.items():
            if cls in seen_classes:
                continue                        # one representative per class
            seen_classes.add(cls)
            n = self._cls_n[cls]
            if n == 0:
                score = 1e9                     # untried class: try once
            else:
                score = self._cls_sum[cls] / n + self.ucb_c * math.sqrt(math.log(total) / n)
            cands.append((score, "hub", url))
        if not cands:
            return None
        if self.rng.random() < self.eps:
            _, kind, item = self.rng.choice(cands)
        else:
            _, kind, item = max(cands, key=lambda c: c[0] + self.rng.random() * 1e-6)
        if kind == "cluster":
            return ("plant", self.rng.choice(tuple(self._todo[item])))
        return ("hub", item)

    # ---------- diagnostics ----------
    def stalled(self, threshold=0.2):
        """True when the last 30 fetches found <0.2 new plants on average."""
        return len(self.recent) == self.recent.maxlen and \
            sum(self.recent) / len(self.recent) < threshold

    def report(self):
        roots = {self._find(k) for k in self.known}
        return {
            "known": len(self.known),
            "fetched": len(self.fetched),
            "clusters": len(roots),
            "explored_clusters": sum(1 for r in roots if self._nfetch[r] > 0),
            "bridges": self.bridges,
        }

    def harvest_list(self):
        """Phase 2: every discovered plant not fetched yet, in random order."""
        todo = sorted(self.known - self.fetched)
        self.rng.shuffle(todo)
        return todo


class SimpleFrontier:
    """Baselines without diversification: 'bfs', 'dfs' or uniform 'random'."""

    def __init__(self, mode="bfs", seed=None):
        self.mode = mode
        self.rng = random.Random(seed)
        self.items = deque()
        self.known = set()
        self.fetched = set()
        self._queued = set()

    def _push(self, kind, item):
        if (kind, item) not in self._queued:
            self._queued.add((kind, item))
            self.items.append((kind, item))

    def add_seed_plant(self, key):
        self.known.add(key)
        self._push("plant", key)

    def next_target(self):
        if not self.items:
            return None
        if self.mode == "bfs":
            return self.items.popleft()
        if self.mode == "dfs":
            return self.items.pop()
        i = self.rng.randrange(len(self.items))
        self.items[i], self.items[-1] = self.items[-1], self.items[i]
        return self.items.pop()

    def _ingest(self, plant_keys, other_urls):
        for url in other_urls:                  # navigation comes first in real HTML
            if not is_forbidden(url):
                self._push("hub", url)
        for key in plant_keys:
            self.known.add(key)
            self._push("plant", key)

    def on_plant_page(self, key, plant_keys, other_urls):
        self.fetched.add(key)
        self._ingest(plant_keys, other_urls)

    def on_hub_page(self, url, plant_keys, other_urls):
        self._ingest(plant_keys, other_urls)


# ======================================================================
# Synthetic benchmark (NOT the real site: it only shows how strategies
# behave on clustered graphs under clearly stated assumptions)
# ======================================================================
class SyntheticSite:
    def __init__(self, n_plants=8000, n_families=350, nav_hubs=0, cats_per_hub=10,
                 cat_size=400, leak=0.0, cap=None, seed=1):
        rng = random.Random(seed)
        weights = [rng.paretovariate(1.1) for _ in range(n_families)]
        sizes = [max(3, round(w / sum(weights) * n_plants)) for w in weights]
        self.n = sum(sizes)
        names = [f"p{i:05d}" for i in range(self.n)]
        rng.shuffle(names)                      # names carry no cluster information
        families, start = [], 0
        for size in sizes:
            families.append(names[start:start + size])
            start += size
        junk = [f"http://x/user/{p}.aspx" for p in
                ("about", "shop", "donate", "blog", "suppliers", "books")]
        top = [f"http://x/user/uses{j}.aspx" for j in range(nav_hubs)]
        nav = top + junk
        self.plants, self.hubs = {}, {}
        for fam in families:
            for member in fam:
                related = [x for x in fam if x != member]
                if cap and len(related) > cap:
                    related = rng.sample(related, cap)
                if leak and rng.random() < leak:
                    related.append(rng.choice(names))
                self.plants[member] = (related, nav)
        for j, url in enumerate(top):
            cats = [f"http://x/user/result.aspx?use=u{j}_{c}" for c in range(cats_per_hub)]
            self.hubs[url] = ([], nav + cats)
            for cat in cats:
                self.hubs[cat] = (rng.sample(names, min(cat_size, self.n)), nav)
        for url in junk:
            self.hubs[url] = ([], nav)
        self.seed_plant = rng.choice(names)


def run(site, frontier, budget=2000, target=0.75):
    frontier.add_seed_plant(site.seed_plant)
    hit, steps = None, 0
    for steps in range(1, budget + 1):
        step = frontier.next_target()
        if step is None:
            steps -= 1
            break
        kind, item = step
        if kind == "plant":
            related, others = site.plants[item]
            frontier.on_plant_page(item, related, others)
        else:
            plants, others = site.hubs.get(item, ([], []))
            frontier.on_hub_page(item, plants, others)
        if hit is None and len(frontier.known) >= target * site.n:
            hit = steps
    return hit, len(frontier.known) / site.n, steps


def benchmark(repeats=3, budget=2000):
    scenarios = [
        ("A: nav hubs + category lists, full family lists", dict(nav_hubs=4)),
        ("B: no hubs, 3% cross-family links", dict(leak=0.03)),
        ("C: no hubs, no cross-family links", dict()),
        ("D: nav hubs, family lists capped at 25", dict(nav_hubs=4, cap=25)),
    ]
    makers = {
        "bfs": lambda s: SimpleFrontier("bfs", s),
        "dfs": lambda s: SimpleFrontier("dfs", s),
        "random": lambda s: SimpleFrontier("random", s),
        "diverse": lambda s: DiverseFrontier(seed=s),
    }
    for title, params in scenarios:
        print(f"\n{title}")
        print(f"  {'strategy':8} {'fetches to 75%':>16} {'coverage @ budget':>19}")
        for name, make in makers.items():
            hits, covs = [], []
            for r in range(repeats):
                site = SyntheticSite(seed=r + 1, **params)
                hit, cov, _ = run(site, make(r), budget=budget)
                hits.append(hit)
                covs.append(cov)
            reached = [h for h in hits if h is not None]
            hit_txt = (f"{sum(reached) / len(reached):.0f} ({len(reached)}/{repeats})"
                       if reached else f">{budget} (0/{repeats})")
            print(f"  {name:8} {hit_txt:>16} {sum(covs) / len(covs):>18.1%}")


def crawl_live(seed_urls, max_fetches=300, sleep=1.5, out_dir="data",
               site_root="https://pfaf.org", contact="meno.priezvisko@stuba.sk"):
    """Polite live crawl driven by DiverseFrontier (needs `requests`).
    seed_urls: a few allowed start pages (plant pages and/or other pages)."""
    import os
    import time
    from urllib.parse import quote_plus
    import requests

    plant_url = site_root + "/user/Plant.aspx?LatinName="
    headers = {"User-Agent": f"VI2026-SkolskyProjekt/1.0 (kontakt: {contact})"}
    raw_dir = os.path.join(out_dir, "raw_html")
    os.makedirs(raw_dir, exist_ok=True)

    frontier = DiverseFrontier(seed=0)
    names = {}                                   # canonical key -> original spelling
    for url in seed_urls:
        plants, _ = split_links(f'<a href="{url}">', site_root)
        if plants:
            names.setdefault(plant_key(plants[0]), plants[0])
            frontier.add_seed_plant(plant_key(plants[0]))
        else:
            frontier.add_seed_page(url)

    for _ in range(max_fetches):
        step = frontier.next_target()
        if step is None or frontier.stalled():
            break
        kind, item = step
        url = plant_url + quote_plus(names[item]) if kind == "plant" else item
        try:
            resp = requests.get(url, headers=headers, timeout=10)
            body = resp.text if resp.status_code == 200 else ""
        except requests.RequestException:
            body = ""
        time.sleep(sleep)
        found, others = split_links(body, url)
        for name in found:
            names.setdefault(plant_key(name), name)
        keys = [plant_key(name) for name in found]
        if kind == "plant":
            if body:
                fname = re.sub(r"[^A-Za-z0-9_+-]", "_", names[item])[:150] + ".html"
                with open(os.path.join(raw_dir, fname), "w", encoding="utf-8") as f:
                    f.write(body)
            frontier.on_plant_page(item, keys, others)
        else:
            frontier.on_hub_page(item, keys, others)
        print(frontier.report())

    with open(os.path.join(out_dir, "discovered.txt"), "w", encoding="utf-8") as f:
        for key in sorted(frontier.known):
            f.write(names.get(key, key) + "\n")
    return frontier, names


if __name__ == "__main__":
    benchmark(repeats=int(sys.argv[1]) if len(sys.argv) > 1 else 3)