"""
spark_common.py - helpers shared by the Spark jobs (spark_extract, spark_wiki, spark_join).

Output files are written with the csv module on the driver, not with df.write:
the results are small (thousands of rows) and Spark's own file output needs
winutils/Hadoop native libraries on Windows.
"""

import csv
import os
import sys
from pathlib import Path

HERE = os.path.dirname(os.path.abspath(__file__))
WORKER_MODULES = ("extractor.py", "extract_all.py", "wiki_parse.py")

csv.field_size_limit(2 ** 31 - 1)


def get_spark(app, master="local[*]", memory="4g"):
    """SparkSession; makes the project modules importable in the Python workers.

    Local mode: the workers are processes on this machine, so PYTHONPATH is enough.
    (sc.addPyFile copies files through Hadoop and needs winutils.exe on Windows.)
    Cluster: the modules are shipped with addPyFile."""
    os.environ.setdefault("PYSPARK_PYTHON", sys.executable)
    os.environ.setdefault("PYSPARK_DRIVER_PYTHON", sys.executable)
    paths = [p for p in os.environ.get("PYTHONPATH", "").split(os.pathsep) if p]
    if HERE not in paths:
        os.environ["PYTHONPATH"] = os.pathsep.join([HERE] + paths)
    from pyspark.sql import SparkSession
    spark = (SparkSession.builder.appName(app).master(master)
             .config("spark.driver.memory", memory)
             .config("spark.ui.enabled", "false")
             .config("spark.sql.shuffle.partitions", "16")
             .getOrCreate())
    spark.sparkContext.setLogLevel("ERROR")
    if not master.startswith("local"):
        for module in WORKER_MODULES:
            spark.sparkContext.addPyFile(os.path.join(HERE, module))
    return spark


def spark_path(path):
    """Local path -> file: URI (also works on Windows); URIs and globs pass through."""
    if "://" in path or any(ch in path for ch in "*?["):
        return path
    return Path(path).resolve().as_uri()


def read_tsv(path):
    with open(path, encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f, delimiter="\t"))


def write_tsv(path, fieldnames, rows):
    os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
    with open(path, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames, delimiter="\t", extrasaction="ignore")
        writer.writeheader()
        for row in rows:
            writer.writerow(row)


def dokuwiki_table(header, rows):
    lines = ["^ " + " ^ ".join(header) + " ^"]
    lines += ["| " + " | ".join(str(cell) for cell in row) + " |" for row in rows]
    return "\n".join(lines)
