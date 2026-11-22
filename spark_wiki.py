"""
spark_wiki.py - Spark job: English Wikipedia dump -> plant articles + redirects.

The dump (enwiki-...-pages-articles*.xml[.bz2]) is cut into <page> records, one Spark
record per page, in one of two ways (--reader): "python" streams every dump file in its
own task (default on Windows, needs no Hadoop file system, parallel over the files of
the dump), "hadoop" uses TextInputFormat with the delimiter "</page>" and splits even
one big bz2 file into blocks. Every record is parsed with
regular expressions (wiki_parse.parse_record). To keep the output small the job keeps
only pages that can be joined with PFAF: taxobox articles and redirects whose genus is
a genus of one of our plants (the set of genera is a broadcast variable).

Outputs (default data/wiki/):
  wiki_taxa.tsv       taxobox articles of PFAF genera (title, taxon, authority, status,
                      common names, lead, categories, sections ...)
  wiki_redirects.tsv  redirects with a PFAF genus in the title (Latin name -> article)
  dump_stats.json     counts for the report

Usage:  python spark_wiki.py DUMP [--plants data/plants_full.tsv] [--out data/wiki]
        DUMP can be one file, a glob (dumps/enwiki*.bz2) or a folder with dump parts.
"""

import argparse
import glob
import json
import os
import sys
import time
from collections import Counter

from spark_common import dokuwiki_table, get_spark, read_tsv, spark_path, write_tsv
from wiki_parse import genus_key, iter_pages, parse_record

TAXA_FIELDS = ["title", "wiki_id", "taxon", "genus", "authority", "status", "image",
               "common_names", "categories", "sections", "text_len", "lead"]
REDIRECT_FIELDS = ["title", "target"]


def scan_partition(records, genera_bc):
    """Parse the records of one partition; yield kept pages and, last, its counters."""
    genera = genera_bc.value
    stats = Counter()
    for record in records:
        if record.startswith("<broken_file>"):   # a damaged dump file: skip it, but report it
            stats["broken_files"] += 1
            print("WARNING damaged dump file, rest skipped:", record, file=sys.stderr)
            continue
        if "<title>" not in record:              # dump header / </mediawiki>
            continue
        stats["pages"] += 1
        page = parse_record(record)
        if page is None:
            continue
        kind = page["kind"]
        stats["ns0_" + kind] += 1
        if kind == "taxon" and genus_key(page["taxon"]) in genera:
            stats["kept_taxon"] += 1
            yield ("taxon", page)
        elif kind == "redirect" and genus_key(page["title"]) in genera:
            stats["kept_redirect"] += 1
            yield ("redirect", page)
    yield ("stats", dict(stats))


def merge_counts(a, b):
    out = Counter(a)
    out.update(b)
    return out


def dump_files(path):
    """Files of the dump: one file, a glob, or a folder with parts."""
    if os.path.isdir(path):
        path = os.path.join(path, "*")
    return sorted(p for p in glob.glob(path) if os.path.isfile(p)) or [path]


def pages_of_files(paths):
    for path in paths:
        try:
            yield from iter_pages(path)
        except (EOFError, OSError, ValueError) as error:     # truncated or damaged file
            yield f"<broken_file>{os.path.basename(path)}: {error}</broken_file>"


def read_dump(sc, path, reader="python"):
    """RDD of <page> records (text up to and including each '</page>').

    python: every dump file is one task and is streamed by iter_pages (bz2 module).
            Needs no Hadoop file system, so it works on Windows without winutils.
            Parallelism = number of files, so use the numbered dump parts.
    hadoop: Hadoop's TextInputFormat with the delimiter '</page>'; a bz2 file is split
            into blocks and read in parallel. Use it on Linux / WSL / a cluster."""
    if reader == "hadoop":
        records = sc.newAPIHadoopFile(
            spark_path(path),
            "org.apache.hadoop.mapreduce.lib.input.TextInputFormat",
            "org.apache.hadoop.io.LongWritable", "org.apache.hadoop.io.Text",
            conf={"textinputformat.record.delimiter": "</page>"})
        return records.map(lambda pair: pair[1])
    files = [os.path.abspath(p) for p in dump_files(path)]
    return sc.parallelize(files, len(files)).mapPartitions(pages_of_files)


def flat(page):
    row = dict(page)
    for key in ("common_names", "categories", "sections"):
        row[key] = "; ".join(page[key])
    return row


def main(argv=None):
    parser = argparse.ArgumentParser(description="Spark processing of the Wikipedia dump")
    parser.add_argument("dump", help="enwiki pages-articles dump (.xml or .xml.bz2, glob allowed)")
    parser.add_argument("--plants", default="data/plants_full.tsv")
    parser.add_argument("--out", default="data/wiki")
    parser.add_argument("--master", default="local[*]")
    parser.add_argument("--memory", default="6g")
    parser.add_argument("--reader", choices=["python", "hadoop"],
                        default="python" if os.name == "nt" else "hadoop",
                        help="python: no Hadoop file system (default on Windows); "
                             "hadoop: splits one big .bz2 file (default elsewhere)")
    args = parser.parse_args(argv)

    started = time.time()
    genera = {genus_key(row["latin_name"]) for row in read_tsv(args.plants)}
    files = dump_files(args.dump)
    dump_mb = sum(os.path.getsize(p) for p in files if os.path.exists(p)) / 1024 / 1024
    print(f"PFAF genera used as a filter: {len(genera)}; dump: {len(files)} file(s), "
          f"{dump_mb:,.1f} MB; reader: {args.reader}")

    spark = get_spark("pfaf-wiki", args.master, args.memory)
    sc = spark.sparkContext
    genera_bc = sc.broadcast(genera)
    scanned = (read_dump(sc, args.dump, args.reader)
               .mapPartitions(lambda it: scan_partition(it, genera_bc))
               .cache())                       # one scan of the dump feeds all three results

    stats = dict(scanned.filter(lambda kv: kv[0] == "stats").values().reduce(merge_counts))
    taxa = sorted((flat(p) for _, p in scanned.filter(lambda kv: kv[0] == "taxon").collect()),
                  key=lambda row: row["title"])
    redirects = sorted((p for _, p in scanned.filter(lambda kv: kv[0] == "redirect").collect()),
                       key=lambda row: row["title"])

    write_tsv(os.path.join(args.out, "wiki_taxa.tsv"), TAXA_FIELDS, taxa)
    write_tsv(os.path.join(args.out, "wiki_redirects.tsv"), REDIRECT_FIELDS, redirects)
    stats.update({"dump_mb": round(dump_mb), "pfaf_genera": len(genera),
                  "seconds": round(time.time() - started, 1)})
    with open(os.path.join(args.out, "dump_stats.json"), "w", encoding="utf-8") as f:
        json.dump(stats, f, indent=2)

    n = lambda key: f"{stats.get(key, 0):,}".replace(",", " ")
    print(dokuwiki_table(["Metric", "Value"], [
        ["Dump size on disk", f"{dump_mb:,.1f} MB".replace(",", " ")],
        ["Pages in the dump", n("pages")],
        ["Articles with taxobox (namespace 0)", n("ns0_taxon")],
        ["Redirects (namespace 0)", n("ns0_redirect")],
        ["Other articles (namespace 0)", n("ns0_article")],
        ["Damaged or truncated dump files (skipped)", n("broken_files")],
        ["Kept: taxobox articles of PFAF genera", n("kept_taxon")],
        ["Kept: redirects with a PFAF genus", n("kept_redirect")],
        ["Processing time", f"{stats['seconds']:.0f} s"],
    ]))
    print(f"\nwritten: {args.out}/wiki_taxa.tsv, wiki_redirects.tsv, dump_stats.json")


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    main()