# PFAF plant search (VI 2026)

Crawler, regex extractor, own TF index with several IDF methods (part 1) and
Spark processing with a Wikipedia join (part 2) for [pfaf.org](https://pfaf.org).
Part 1 uses only the Python standard library (`requests` for the crawler, `tiktoken`
optional for token counts); part 2 needs `pyspark` and Java 17+.
No database, no pandas, no NLTK.

## Part 1: crawl, extract, index

```
pip install requests tiktoken
python pfaf_crawler.py --max-fetches 0   # crawl everything, resumable (data/pages.tsv)
python extract_all.py                    # data/raw_html -> data/plants_full.tsv
python indexer.py build                  # data/index/
python indexer.py search "edible fruit shade" --idf smooth -k 10
python indexer.py compare "nitrogen fixing hedge"   # DokuWiki tables, all IDF methods
python stats.py                          # statistics table for the report
python -m unittest test_extractor test_indexer -v
```

Edit the contact address in `HEADERS` (`pfaf_crawler.py`) before crawling.
`retry_failed.py` re-downloads plants that failed with a timeout.

## Part 2: Spark and Wikipedia

```
pip install pyspark                      # needs a JDK 17 or newer (java -version)
python spark_extract.py --out data/plants_full_spark.tsv     # extraction as a Spark job
python spark_wiki.py D:\dumps\enwiki-latest-pages-articles.xml.bz2   # dump -> data/wiki/
python spark_join.py                     # data/plants_full.tsv + data/wiki -> data/joined/
python -m unittest test_wiki_parse test_spark_pipeline -v   # parser and Spark tests
```

* `spark_wiki.py` accepts one file, a glob (`dumps\enwiki*.bz2`) or a folder with dump parts.
  A numbered part of the dump (`enwiki-latest-pages-articlesN.xml-p...bz2`) is enough for a
  trial run. On Windows download **all numbered parts** into one folder and pass the folder:
  `python spark_wiki.py D:\dumps\enwiki`. Each part is one Spark task, so all cores work.
* `--reader python` (default on Windows) streams every dump file with Python's `bz2` module
  and needs no Hadoop file system. `--reader hadoop` (default on Linux/WSL) splits even one
  big `.bz2` file into blocks. Both give the same result (checked by a test).
* Measured here: about 20 MB/s of uncompressed wikitext per core (decompression dominates).
* All jobs run with `--master local[*]`; the same code runs on a cluster by passing another
  `--master`. Use `--memory 8g` for the full dump.
* No `winutils.exe` is needed: the jobs avoid Hadoop file operations (the workers read the
  files themselves, results are written with the csv module, not with `df.write`). If Spark
  cannot start a Python worker, set `PYSPARK_PYTHON` to your python.exe.

## Files

| File | Purpose |
|---|---|
| `pfaf_crawler.py` | crawler: robots.txt, retries, resume, saves HTML and logs every fetch |
| `diverse_frontier.py` | frontier that prefers pages revealing new plant clusters |
| `extractor.py` | regex extraction of 27 fields by element id |
| `extract_all.py` | re-extracts all saved pages, prints field coverage |
| `indexer.py` | inverted TF index, IDF methods `classic`, `smooth`, `max`, CLI search |
| `stats.py` | crawl statistics, tiktoken token counts |
| `retry_failed.py` | retries failed plant downloads |
| `spark_common.py` | SparkSession, paths, TSV helpers shared by the Spark jobs |
| `spark_extract.py` | PFAF extraction as a Spark job (same result as `extract_all.py`) |
| `wiki_parse.py` | regex parser of dump records: taxobox, lead, categories, redirects, name keys |
| `spark_wiki.py` | Spark job: Wikipedia dump -> taxobox articles and redirects of PFAF genera |
| `spark_join.py` | Spark job: join of PFAF plants with Wikipedia articles, join statistics |
| `test_extractor.py`, `test_indexer.py` | unit tests of part 1 |
| `test_wiki_parse.py`, `test_spark_pipeline.py` | unit tests of the parser and the Spark pipeline |
