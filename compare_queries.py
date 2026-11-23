"""
compare_queries.py - old index (own TF index, part 1) versus new index (Lucene), query by query.

    python compare_queries.py                   # DokuWiki tables for the report
    python compare_queries.py --idf classic -k 10

Needs both indexes:  python indexer.py build   and   python lucene_search.py build
and PyLucene (run it in the Docker image, see README.md). The old index works on
data/plants_full.tsv, the new one on data/joined/plants_wiki.tsv (same plants, plus the
wiki_* fields), so results are compared by Latin name.
"""

import argparse
import os
import sys
import time

import indexer
from lucene_index import INDEX_DIR, Searcher

# (label, what is tested, old query or None if the old index cannot express it,
#  new query, options of the new search)
CASES = [
    ("Several words (OR)", "bag of words", "edible fruit shade tolerant",
     "edible fruit shade tolerant", {}),
    ("Several words (AND)", "Boolean AND", "edible fruit shade tolerant",
     "edible fruit shade tolerant", {"operator": "and"}),
    ("Common name from Wikipedia", "wiki_common_names, wiki_title", "silver fir", "silver fir", {}),
    ("Phrase", "Phrase", "nitrogen fixing", '"nitrogen fixing"', {}),
    ("Phrase with slop", "Phrase (slop 2)", "wound healing", '"wound healing"~2', {}),
    ("Common name (AND)", "Boolean AND on names", "silver fir", "silver fir", {"operator": "and"}),
    ("Typo in a name", "Fuzzy", "rosmarinus offcinalis", "rosmarinus offcinalis", {"fuzzy": True}),
    ("Typo in the only word", "Fuzzy", "rosmarnus", "rosmarnus", {"fuzzy": True}),
    ("Exclusion", "Boolean NOT", None, "nitrogen fixing -tree", {}),
    ("Prefix of a Latin name", "Prefix/Wildcard", None, "latin_name:abies*", {}),
    ("Plants of one family", "Filter (family)", "fabaceae nitrogen fixing",
     "family:fabaceae nitrogen fixing", {}),
    ("Edible and medicinal rating", "Range (IntPoint)", None, "edibility>=4 medicinal>=4", {}),
    ("Hardiness zone and rating", "Range (2 fields)", None, "hardiness:6 edibility>=3 shade", {}),
    ("Wikipedia category", "Filter (category)", None, 'category:"medicinal plants" wound healing', {}),
    ("IUCN status", "Filter (status)", None, "status:vu", {}),
]


def old_search(index, query, method, k):
    """(hits, [latin names]) of the part-1 index; hits = documents with at least one term."""
    docs = set()
    for term in set(indexer.tokenize(query)):
        docs.update(doc_id for doc_id, _ in index.postings.get(term, ()))
    names = [index.docs[doc_id]["latin_name"] for doc_id, _ in index.search(query, method, k)]
    return len(docs), names


def timed(function, repeat=20):
    function()                                              # warm-up (JIT, caches)
    started = time.perf_counter()
    for _ in range(repeat):
        function()
    return (time.perf_counter() - started) / repeat * 1000.0


def folder_mb(path):
    total = sum(os.path.getsize(os.path.join(root, name))
                for root, _, names in os.walk(path) for name in names)
    return total / 1024 / 1024


def main(argv=None):
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description="compare the old and the Lucene index")
    parser.add_argument("--idf", choices=list(indexer.IDF_METHODS), default="smooth")
    parser.add_argument("-k", type=int, default=10)
    parser.add_argument("--old-index", default=indexer.INDEX_DIR)
    parser.add_argument("--new-index", default=INDEX_DIR)
    args = parser.parse_args(argv)
    k = args.k

    old = indexer.Index(args.old_index)
    rows = []
    with Searcher(args.new_index) as new:
        print(f"===== Index size =====\n\n^ Index ^ Documents ^ On disk ^\n"
              f"| old (own TF index) | {old.n_docs} | {folder_mb(args.old_index):.1f} MB |\n"
              f"| new (Lucene) | {new.num_docs} | {folder_mb(args.new_index):.1f} MB |\n")
        for label, kind, old_query, new_query, options in CASES:
            print(f"\n===== {label} ({kind}) =====\n")
            outcome = new.search(new_query, k, **options)
            new_names = [r["latin_name"] for r in outcome["results"]]
            if old_query is None:
                old_hits, old_names, old_ms = None, [], None
            else:
                old_hits, old_names = old_search(old, old_query, args.idf, k)
                old_ms = timed(lambda: old.search(old_query, args.idf, k))
            new_ms = timed(lambda: new.search(new_query, k, **options))
            print("old index: " + (repr(old_query) if old_query else "cannot express this query"))
            print(f"new index: {new_query!r}" + (f" ({options})" if options else ""))
            print(f"Lucene query (shortened): ''{outcome['query'][:160]}''\n")
            print("^ # ^ old index ^ new index (Lucene) ^")
            for rank in range(k):
                left = old_names[rank] if rank < len(old_names) else ""
                right = (f"{outcome['results'][rank]['latin_name']} ({outcome['results'][rank]['score']:.2f})"
                         if rank < len(outcome["results"]) else "")
                print(f"| {rank + 1} | {left} | {right} |")
            overlap = len(set(old_names) & set(new_names)) if old_query else None
            rows.append((label, kind, old_hits, outcome["hits"], overlap, old_ms, new_ms))

    print("\n===== Summary =====\n")
    print(f"^ Query ^ Type ^ Hits old ^ Hits new ^ Overlap@{k} ^ Time old (ms) ^ Time new (ms) ^")
    for label, kind, old_hits, new_hits, overlap, old_ms, new_ms in rows:
        print(f"| {label} | {kind} | {'-' if old_hits is None else old_hits} | {new_hits} | "
              f"{'-' if overlap is None else overlap} | {'-' if old_ms is None else f'{old_ms:.1f}'} | "
              f"{new_ms:.1f} |")
    print(f"\n(old index: IDF '{args.idf}'; hits = documents matching the query; "
          f"'-' = the old index cannot express the query)")


if __name__ == "__main__":
    main()