
import argparse
import csv
import heapq
import json
import math
import os
import re
import sys
import time
from collections import Counter, defaultdict

from extractor import strip_references


DATA_DIR = "data"
PLANTS_PATH = os.path.join(DATA_DIR, "plants_full.tsv")
INDEX_DIR = os.path.join(DATA_DIR, "index")

# Integer weight = how many times the tokens of a field are counted.
FIELD_WEIGHTS = {
    "latin_name": 3, "common_name": 3, "family": 2, "synonyms": 2, "other_names": 1,
    "summary": 1, "physical_characteristics": 1, "edible_uses": 1, "medicinal_uses": 1,
    "other_uses": 1, "special_uses": 1, "cultivation": 1, "propagation": 1,
    "found_in": 1, "habitats": 1, "range": 1, "known_hazards": 1, "plant_habitats": 1,
    "care": 1, "weed_potential_text": 1, "carbon_farming": 1, "conservation_status": 1,
}

STOPWORDS = frozenset(
    "a an and are as at be but by can for from has have if in into is it its may not of "
    "on or that the their there these they this to was were when which will with".split())
TOKEN_RE = re.compile(r"[^\W_]+")

DEFAULT_QUERIES = ["edible fruit shade tolerant", "nitrogen fixing hedge",
                   "wound healing poultice"]


def tokenize(text):
    """Lower-case words (letters/digits, any language); no stemming or lemmatization."""
    return [t for t in TOKEN_RE.findall(text.lower()) if len(t) > 1 and t not in STOPWORDS]


# ---------------------------------------------------------------- IDF methods
def idf_classic(n_docs, df, max_df):
    return math.log10(n_docs / df)


def idf_smooth(n_docs, df, max_df):
    return math.log((n_docs + 1) / (df + 1)) + 1.0


def idf_max(n_docs, df, max_df):
    return max(0.0, math.log(max_df / (1 + df)))


IDF_METHODS = {"classic": idf_classic, "smooth": idf_smooth, "max": idf_max}


# ---------------------------------------------------------------- building
def build_index(plants_path=PLANTS_PATH, index_dir=INDEX_DIR):
    """Read plants_full.tsv and write postings.tsv, docs.tsv and meta.json."""
    started = time.time()
    csv.field_size_limit(2 ** 31 - 1)
    postings = defaultdict(dict)                   # term -> {doc_id: weighted tf}
    docs = []
    with open(plants_path, encoding="utf-8", newline="") as f:
        for doc_id, row in enumerate(csv.DictReader(f, delimiter="\t")):
            counts = Counter()
            for field, weight in FIELD_WEIGHTS.items():
                for token in tokenize(strip_references(row.get(field, "") or "")):
                    counts[token] += weight
            norm = math.sqrt(sum((1 + math.log(tf)) ** 2 for tf in counts.values())) or 1.0
            for term, tf in counts.items():
                postings[term][doc_id] = tf
            docs.append((row["latin_name"], row.get("common_name", ""),
                         row.get("family", ""), sum(counts.values()), f"{norm:.6f}"))

    os.makedirs(index_dir, exist_ok=True)
    with open(os.path.join(index_dir, "postings.tsv"), "w", encoding="utf-8") as f:
        for term in sorted(postings):
            plist = postings[term]
            f.write(f"{term}\t{len(plist)}\t"
                    + ",".join(f"{d}:{tf}" for d, tf in plist.items()) + "\n")
    with open(os.path.join(index_dir, "docs.tsv"), "w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f, delimiter="\t")
        writer.writerow(["latin_name", "common_name", "family", "length", "norm"])
        writer.writerows(docs)
    meta = {"documents": len(docs), "vocabulary": len(postings),
            "postings": sum(len(p) for p in postings.values()),
            "max_df": max((len(p) for p in postings.values()), default=0),
            "build_seconds": round(time.time() - started, 2)}
    with open(os.path.join(index_dir, "meta.json"), "w", encoding="utf-8") as f:
        json.dump(meta, f, indent=2)
    return meta


# ---------------------------------------------------------------- searching
class Index:
    def __init__(self, index_dir=INDEX_DIR):
        with open(os.path.join(index_dir, "meta.json"), encoding="utf-8") as f:
            meta = json.load(f)
        self.n_docs, self.max_df = meta["documents"], meta["max_df"]
        self.docs = []
        with open(os.path.join(index_dir, "docs.tsv"), encoding="utf-8", newline="") as f:
            for row in csv.DictReader(f, delimiter="\t"):
                row["norm"] = float(row["norm"])
                self.docs.append(row)
        self.postings = {}
        with open(os.path.join(index_dir, "postings.tsv"), encoding="utf-8") as f:
            for line in f:
                term, _, plist = line.rstrip("\n").split("\t")
                self.postings[term] = [tuple(map(int, p.split(":"))) for p in plist.split(",")]

    def df(self, term):
        return len(self.postings.get(term, ()))

    def idf(self, term, method):
        return IDF_METHODS[method](self.n_docs, self.df(term), self.max_df)

    def search(self, query, method="classic", k=10):
        """Top-k (doc_id, score) for the query using the chosen IDF method."""
        scores = defaultdict(float)
        for term, qtf in Counter(tokenize(query)).items():
            plist = self.postings.get(term)
            if not plist:
                continue
            weight = (1 + math.log(qtf)) * self.idf(term, method)
            for doc_id, tf in plist:
                scores[doc_id] += weight * (1 + math.log(tf)) / self.docs[doc_id]["norm"]
        best = heapq.nlargest(k, scores.items(), key=lambda item: (item[1], -item[0]))
        return [(doc_id, score) for doc_id, score in best if score > 0]


# ---------------------------------------------------------------- CLI helpers
def print_results(index, query, method, k):
    results = index.search(query, method, k)
    if not results:
        print("  no results")
    for rank, (doc_id, score) in enumerate(results, 1):
        doc = index.docs[doc_id]
        print(f"{rank:>3}. {score:7.3f}  {doc['latin_name']}  |  {doc['common_name']}"
              f"  |  {doc['family']}")


def compare(index, queries, k=10):
    """DokuWiki tables: term IDFs, top-k per method, overlap between methods."""
    methods = list(IDF_METHODS)
    for query in queries:
        terms = sorted(set(tokenize(query)))
        print(f"\n===== Query: {query} =====\n")
        print("^ term ^ df ^ " + " ^ ".join(f"idf {m}" for m in methods) + " ^")
        for term in terms:
            if index.df(term):
                print(f"| {term} | {index.df(term)} | "
                      + " | ".join(f"{index.idf(term, m):.3f}" for m in methods) + " |")
            else:
                print(f"| {term} | 0 | " + " | ".join("-" for _ in methods) + " |")
        results = {m: index.search(query, m, k) for m in methods}
        print("\n^ # ^ " + " ^ ".join(methods) + " ^")
        for rank in range(k):
            cells = []
            for m in methods:
                if rank < len(results[m]):
                    doc_id, score = results[m][rank]
                    cells.append(f"{index.docs[doc_id]['latin_name']} ({score:.2f})")
                else:
                    cells.append("")
            print(f"| {rank + 1} | " + " | ".join(cells) + " |")
        sets = {m: {d for d, _ in results[m]} for m in methods}
        print()
        for i, a in enumerate(methods):
            for b in methods[i + 1:]:
                common = len(sets[a] & sets[b])
                print(f"  overlap@{k} {a} vs {b}: {common}/{k}")


def main():
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description="own TF index with several IDF methods")
    sub = parser.add_subparsers(dest="command", required=True)
    sub.add_parser("build", help="build the index from data/plants_full.tsv")
    p_search = sub.add_parser("search", help="ranked search (interactive without a query)")
    p_search.add_argument("query", nargs="?")
    p_search.add_argument("--idf", choices=list(IDF_METHODS), default="classic")
    p_search.add_argument("-k", type=int, default=10)
    p_cmp = sub.add_parser("compare", help="compare IDF methods on several queries")
    p_cmp.add_argument("queries", nargs="*")
    p_cmp.add_argument("-k", type=int, default=10)
    args = parser.parse_args()

    if args.command == "build":
        meta = build_index()
        size = sum(os.path.getsize(os.path.join(INDEX_DIR, n)) for n in os.listdir(INDEX_DIR))
        print(f"index built in {meta['build_seconds']} s: {meta['documents']} documents, "
              f"{meta['vocabulary']} terms, {meta['postings']} postings, "
              f"{size / 1024 / 1024:.1f} MB on disk")
        return
    index = Index()
    if args.command == "compare":
        compare(index, args.queries or DEFAULT_QUERIES, args.k)
    elif args.query:
        print_results(index, args.query, args.idf, args.k)
    else:
        while True:
            query = input("query> ").strip()
            if not query:
                break
            print_results(index, query, args.idf, args.k)


if __name__ == "__main__":
    main()