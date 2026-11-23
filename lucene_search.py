"""
lucene_search.py - the application on top of the Lucene index (build + search).

    python lucene_search.py build                       # data/joined/plants_wiki.tsv -> data/index_lucene
    python lucene_search.py search "silver fir"         # OR (default), ranked by BM25
    python lucene_search.py search "edible fruit shade" --mode and
    python lucene_search.py search '"nitrogen fixing" family:Fabaceae edibility>=3'
    python lucene_search.py search "rosmarinus offcinalis" --fuzzy
    python lucene_search.py search                      # interactive prompt
    python lucene_search.py search "silver fir" --explain   # BM25 breakdown of the best hit

The same search is reachable from the part-1 application:
    python indexer.py search "silver fir" --engine lucene

Query language (see query_syntax.py): AND OR NOT + -, "phrase"~slop, word~1 (fuzzy),
prefix*, field:value, family:/category:/status:/care:/match:/wiki:, edibility>=4,
medicinal>=3, hardiness:7, wikilen>=20000.
Run it where PyLucene is installed (Docker image coady/pylucene, see README.md).
"""

import argparse
import sys

from lucene_index import INDEX_DIR, PLANTS_WIKI_PATH, Searcher, build_index


def print_results(outcome, with_query=False):
    if with_query:
        print(f"  query: {outcome['query']}")
    print(f"  {outcome['hits']} hits")
    for item in outcome["results"]:
        wiki = f"  | wiki: {item['wiki_title']}" if item["wiki_title"] else ""
        print(f"{item['rank']:>3}. {item['score']:7.3f}  {item['latin_name']}  |  "
              f"{item['common_name']}  |  {item['family']}{wiki}")


def run_search(query, k=10, mode="or", fuzzy=False, explain=False, index_dir=INDEX_DIR):
    """Print the results of one query (or run an interactive prompt without a query)."""
    with Searcher(index_dir) as searcher:
        queries = [query] if query else iter(lambda: input("query> ").strip(), "")
        for text in queries:
            outcome = searcher.search(text, k, mode, fuzzy)
            print_results(outcome, with_query=bool(explain))
            if explain and outcome["results"]:
                print(searcher.explain(text, outcome["results"][0]["doc_id"], mode, fuzzy))


def main(argv=None):
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(description="PyLucene index of the PFAF + Wikipedia data")
    sub = parser.add_subparsers(dest="command", required=True)
    p_build = sub.add_parser("build", help="build the Lucene index")
    p_build.add_argument("--plants", default=PLANTS_WIKI_PATH)
    p_build.add_argument("--index", default=INDEX_DIR)
    p_search = sub.add_parser("search", help="ranked search (interactive without a query)")
    p_search.add_argument("query", nargs="?")
    p_search.add_argument("-k", type=int, default=10)
    p_search.add_argument("--mode", choices=["or", "and"], default="or",
                          help="default operator between words (default or)")
    p_search.add_argument("--fuzzy", action="store_true", help="allow one typo per word (word~1)")
    p_search.add_argument("--explain", action="store_true", help="show the query and BM25 explanation")
    p_search.add_argument("--index", default=INDEX_DIR)
    args = parser.parse_args(argv)

    if args.command == "build":
        meta = build_index(args.plants, args.index)
        print(f"Lucene index built in {meta['seconds']} s: {meta['documents']} documents "
              f"({meta['with_wiki']} with a Wikipedia article), {meta['size_mb']} MB on disk")
    else:
        run_search(args.query, args.k, args.mode, args.fuzzy, args.explain, args.index)


if __name__ == "__main__":
    main()
