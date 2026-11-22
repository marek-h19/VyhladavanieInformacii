"""
spark_extract.py - the extraction of part 1 (extract_all.py) as a Spark job.

raw_html/*.html  ->  (latin name, fields)  ->  duplicates merged  ->  plants TSV

The extraction itself is unchanged (extractor.extract_row, regex only); Spark only
distributes it: the list of saved pages is split between the workers, every worker reads
its pages and applies the regex extractor, reduceByKey merges duplicate plants and an
aggregate counts the filled fields.

Usage:  python spark_extract.py [--data data] [--out data/plants_full_spark.tsv]
        python spark_extract.py --out data/plants_full.tsv     (replace the part-1 file)
"""

import argparse
import glob
import os
import sys
import time

from extract_all import names_from_pages_log, resolve_name
from extractor import FIELDNAMES, extract_row
from spark_common import get_spark, write_tsv


def main(argv=None):
    parser = argparse.ArgumentParser(description="Spark extraction of saved PFAF pages")
    parser.add_argument("--data", default="data", help="folder with raw_html/ and pages.tsv")
    parser.add_argument("--out", default=None, help="output TSV (default <data>/plants_full_spark.tsv)")
    parser.add_argument("--master", default="local[*]")
    parser.add_argument("--memory", default="4g")
    args = parser.parse_args(argv)
    out_path = args.out or os.path.join(args.data, "plants_full_spark.tsv")

    started = time.time()
    spark = get_spark("pfaf-extract", args.master, args.memory)
    sc = spark.sparkContext
    mapping = sc.broadcast(names_from_pages_log(args.data))

    def extract(path):
        base = os.path.basename(path)
        with open(path, encoding="utf-8", errors="replace") as f:     # read inside the worker
            text = f.read()
        name = resolve_name(base, text, mapping.value)
        return name.lower(), (base, extract_row(name, text))

    # The RDD holds only the file names; each task opens its own files. No Hadoop file
    # system is involved (it would need winutils.exe on Windows); a cluster would need
    # the folder on shared storage.
    paths = sorted(glob.glob(os.path.join(args.data, "raw_html", "*.html")))
    n_files = len(paths)
    pages = sc.parallelize([os.path.abspath(p) for p in paths], sc.defaultParallelism * 4)
    # same plant saved twice (old + new crawler): keep the first file name, like extract_all.py
    plants = (pages.map(extract)
                   .reduceByKey(lambda a, b: a if a[0] <= b[0] else b)
                   .mapValues(lambda value: value[1])
                   .cache())
    total = plants.count()

    fields = FIELDNAMES[1:]
    filled = plants.map(lambda kv: [1 if kv[1][f] else 0 for f in fields]).reduce(
        lambda a, b: [x + y for x, y in zip(a, b)]) if total else [0] * len(fields)
    empty_family = sorted(row["latin_name"] for _, row in plants.collect() if not row["family"])

    rows = [row for _, row in plants.sortByKey().toLocalIterator()]
    write_tsv(out_path, FIELDNAMES, rows)

    print(f"files read: {n_files}, unique plants: {total}, duplicates skipped: {n_files - total}")
    print(f"written: {out_path}  ({time.time() - started:.1f} s, master {args.master})\n")
    print(f"{'field':26} {'filled':>7} {'percent':>8}")
    for field, count in zip(fields, filled):
        print(f"{field:26} {count:>7} {100 * count / max(total, 1):>8.2f}%")
    print(f"\npages with empty family: {len(empty_family)}")
    for name in empty_family[:10]:
        print("  ", name)


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    main()
