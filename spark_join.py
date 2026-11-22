"""
spark_join.py - Spark job: join PFAF plants with Wikipedia articles by Latin name.

Inputs : data/plants_full.tsv            (our extracted plants)
         data/wiki/wiki_taxa.tsv         (taxobox articles, from spark_wiki.py)
         data/wiki/wiki_redirects.tsv    (redirects, from spark_wiki.py)
Outputs: data/joined/plants_wiki.tsv     every plant + wiki_* columns (empty when no match)
         data/joined/unmatched.txt       plants without a Wikipedia article

Join logic. English Wikipedia often keeps a species under its common name (the article
"Okra" is the page of Abelmoschus esculentus, and "Abelmoschus esculentus" is only a
redirect). So every article has several "aliases", each with a priority:
    1  taxon name in the taxobox          (Okra            <- abelmoschus esculentus)
    2  article title, if it differs       (a Latin title the taxobox did not give)
    3  redirect title                     (Abelmoschus esculentus -> Okra)
A PFAF name is joined on its normalised full name (priority 1 to 3). Plants without
a hit are joined once more on genus + species only (score + 10, match "binomial"),
which maps subspecies, varieties and cultivars to the species article. Per plant the
alias with the lowest score wins (window function), so a plant gets at most one page.

Usage:  python spark_join.py [--plants data/plants_full.tsv] [--wiki data/wiki] [--out data/joined]
"""

import argparse
import os
import sys
import time

from spark_common import dokuwiki_table, get_spark, read_tsv, write_tsv
from wiki_parse import binomial_key, latin_key

WIKI_COLUMNS = ["wiki_title", "wiki_match", "wiki_id", "wiki_taxon", "wiki_authority",
                "wiki_status", "wiki_common_names", "wiki_lead", "wiki_categories",
                "wiki_sections", "wiki_image", "wiki_text_len"]
TAXA_TO_WIKI = {"wiki_id": "wiki_id", "wiki_taxon": "taxon", "wiki_authority": "authority",
                "wiki_status": "status", "wiki_common_names": "common_names",
                "wiki_lead": "lead", "wiki_categories": "categories",
                "wiki_sections": "sections", "wiki_image": "image", "wiki_text_len": "text_len"}


def taxon_aliases(row):
    """(alias_key, title, priority, how) for one taxobox article."""
    aliases = [(latin_key(row["taxon"]), row["title"], 1, "taxobox")]
    title_key = latin_key(row["title"])
    if title_key != aliases[0][0]:
        aliases.append((title_key, row["title"], 2, "title"))
    return aliases


def main(argv=None):
    parser = argparse.ArgumentParser(description="Spark join of PFAF plants and Wikipedia")
    parser.add_argument("--plants", default="data/plants_full.tsv")
    parser.add_argument("--wiki", default="data/wiki")
    parser.add_argument("--out", default="data/joined")
    parser.add_argument("--master", default="local[*]")
    parser.add_argument("--memory", default="4g")
    args = parser.parse_args(argv)

    from pyspark.sql import Window
    from pyspark.sql import functions as F

    started = time.time()
    plants = read_tsv(args.plants)
    taxa = read_tsv(os.path.join(args.wiki, "wiki_taxa.tsv"))
    redirects = read_tsv(os.path.join(args.wiki, "wiki_redirects.tsv"))

    spark = get_spark("pfaf-join", args.master, args.memory)
    sc = spark.sparkContext
    titles_bc = sc.broadcast({row["title"] for row in taxa})

    plants_df = spark.createDataFrame(
        [(p["latin_name"], latin_key(p["latin_name"]), binomial_key(p["latin_name"]))
         for p in plants], "latin_name string, key string, bkey string")
    taxa_df = spark.createDataFrame(
        [(t["title"], t["taxon"], t["status"], t["lead"], t["common_names"],
          t["categories"], t["image"], t["authority"]) for t in taxa],
        "title string, taxon string, status string, lead string, common_names string, "
        "categories string, image string, authority string")

    # aliases: taxobox name / title from articles, redirect titles pointing at a kept article
    alias_rdd = sc.parallelize(taxa, 8).flatMap(taxon_aliases).union(
        sc.parallelize(redirects, 8)
          .filter(lambda r: r["target"] in titles_bc.value)
          .map(lambda r: (latin_key(r["title"]), r["target"], 3, "redirect")))
    alias_df = (spark.createDataFrame(
        alias_rdd, "alias_key string, title string, priority int, how string").distinct())

    exact = (plants_df.join(alias_df, plants_df.key == alias_df.alias_key)
             .select("latin_name", "title", "how", F.col("priority").alias("score")))
    binomial = (plants_df.join(alias_df, plants_df.bkey == alias_df.alias_key)
                .select("latin_name", "title", F.lit("binomial").alias("how"),
                        (F.col("priority") + 10).alias("score")))
    best = (exact.unionByName(binomial)
            .withColumn("rn", F.row_number().over(
                Window.partitionBy("latin_name").orderBy("score", "title")))
            .filter("rn = 1").drop("rn").cache())

    n_plants = plants_df.count()
    n_matched = best.count()
    n_pages = best.select("title").distinct().count()
    by_how = {r["how"]: r["count"] for r in best.groupBy("how").count().collect()}
    shared = (best.groupBy("title").count().filter("count > 1").count())
    unmatched = sorted(r["latin_name"] for r in
                       plants_df.join(best, "latin_name", "left_anti").collect())
    joined = best.join(taxa_df, "title")
    has = lambda col: F.sum(F.when(F.col(col) != "", 1).otherwise(0)).alias(col)
    coverage = joined.agg(has("lead"), has("status"), has("common_names"),
                          has("categories"), has("image"), has("authority")).first().asDict()

    # assemble the output on the driver: every plant, wiki columns empty without a match
    match = {r["latin_name"]: (r["title"], r["how"]) for r in best.collect()}
    by_title = {t["title"]: t for t in taxa}
    out_rows = []
    for plant in plants:
        row = dict(plant)
        hit = match.get(plant["latin_name"])
        if hit:
            page = by_title[hit[0]]
            row.update(wiki_title=hit[0], wiki_match=hit[1])
            for column, source in TAXA_TO_WIKI.items():
                row[column] = page[source]
        out_rows.append(row)
    write_tsv(os.path.join(args.out, "plants_wiki.tsv"),
              list(plants[0].keys()) + WIKI_COLUMNS, out_rows)
    os.makedirs(args.out, exist_ok=True)
    with open(os.path.join(args.out, "unmatched.txt"), "w", encoding="utf-8") as f:
        f.write("\n".join(unmatched) + "\n")

    pct = lambda a, b: f"{100 * a / max(b, 1):.1f} %"
    exact_matched = n_matched - by_how.get("binomial", 0)
    print(dokuwiki_table(["Metric", "Value"], [
        ["PFAF plants", n_plants],
        ["Taxobox articles from the dump (PFAF genera)", len(taxa)],
        ["Redirects from the dump (PFAF genera)", len(redirects)],
        ["PFAF plants joined with a Wikipedia article", f"{n_matched} ({pct(n_matched, n_plants)})"],
        ["Unique Wikipedia pages joined", n_pages],
        ["Pages joined with 2 or more PFAF plants", shared],
        ["Plants without an article", f"{len(unmatched)} ({pct(len(unmatched), n_plants)})"],
    ]))
    print()
    print(dokuwiki_table(["Match type", "PFAF plants"], [
        ["taxobox (taxon name in the infobox)", by_how.get("taxobox", 0)],
        ["title (Latin article title)", by_how.get("title", 0)],
        ["redirect (Latin name redirects to the article)", by_how.get("redirect", 0)],
        ["binomial (subspecies, variety, cultivar -> species article)", by_how.get("binomial", 0)],
        ["exact matches together", f"{exact_matched} ({pct(exact_matched, n_plants)})"],
    ]))
    print()
    print(dokuwiki_table(["Wiki attribute", "Joined plants with a value"], [
        [name, f"{count} ({pct(count, n_matched)})"] for name, count in coverage.items()]))
    print(f"\nunmatched examples: {', '.join(unmatched[:12])}")
    print(f"written: {args.out}/plants_wiki.tsv, unmatched.txt  ({time.time() - started:.1f} s)")


if __name__ == "__main__":
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    main()
