
import argparse
import csv
import glob
import os
import re
from collections import Counter


DATA_DIR = "data"


def dir_stats(folder):
    files = glob.glob(os.path.join(DATA_DIR, folder, "*"))
    return len(files), sum(os.path.getsize(p) for p in files)


def own_tokens(text):
    return len(re.findall(r"[^\W_]+", text))


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--html", action="store_true", help="also count raw HTML tokens")
    args = parser.parse_args()

    # pages.tsv is an append-only log (resume and retry runs add rows), so count
    # every URL once: the last row of a URL wins, except that a saved file is never lost.
    latest = {}
    with open(os.path.join(DATA_DIR, "pages.tsv"), encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f, delimiter="\t"):
            previous = latest.get(row["url"])
            if previous and previous["file"] and not row["file"]:
                continue
            latest[row["url"]] = row
    kinds, plant_ok, plant_failed = Counter(), 0, Counter()
    for row in latest.values():
        kinds[row["kind"]] += 1
        if row["kind"] == "plant" and row["status"] == "200" and row["file"]:
            plant_ok += 1
        elif row["kind"] == "plant":
            plant_failed[row["status"]] += 1

    plant_files, plant_bytes = dir_stats("raw_html")
    other_files, other_bytes = dir_stats("other_html")

    text_chars, own_total, rows, with_data = 0, 0, [], 0
    path = os.path.join(DATA_DIR, "plants_full.tsv")
    if os.path.exists(path):
        with open(path, encoding="utf-8", newline="") as f:
            for row in csv.DictReader(f, delimiter="\t"):
                text = " ".join(row.values())
                rows.append(text)
                with_data += 1 if row.get("family") else 0
                text_chars += len(text)
                own_total += own_tokens(text)

    tiktoken_total = html_tokens = None
    try:
        import tiktoken
        enc = tiktoken.get_encoding("cl100k_base")
        tiktoken_total = sum(len(enc.encode_ordinary(t)) for t in rows)
        if args.html:
            html_tokens = 0
            for p in glob.glob(os.path.join(DATA_DIR, "raw_html", "*.html")):
                with open(p, encoding="utf-8", errors="replace") as f:
                    html_tokens += len(enc.encode_ordinary(f.read()))
    except ImportError:
        print("(pip install tiktoken to get token counts)")

    mb = 1024 * 1024
    table = [
        ("Fetched pages (all)", sum(kinds.values())),
        ("Plant pages fetched (status 200, saved)", plant_ok),
        ("Plant fetches without saved HTML (status: count)",
         ", ".join(f"{s}: {c}" for s, c in sorted(plant_failed.items())) or "none"),
        ("Other internal pages fetched", kinds["hub"]),
        ("Plants in plants_full.tsv", len(rows)),
        ("Plants with data (family filled)", with_data),
        ("Plant HTML on disk", f"{plant_bytes / mb:.1f} MB ({plant_files} files)"),
        ("Other HTML on disk", f"{other_bytes / mb:.1f} MB ({other_files} files)"),
        ("Extracted text", f"{text_chars / mb:.1f} MB"),
        ("Tokens, own regex tokenizer", own_total),
        ("Tokens, tiktoken cl100k_base (extracted text)", tiktoken_total),
    ]
    if html_tokens is not None:
        table.append(("Tokens, tiktoken (raw HTML)", html_tokens))
    print("^ Metric ^ Value ^")
    for name, value in table:
        print(f"| {name} | {'n/a (tiktoken not installed)' if value is None else value} |")


if __name__ == "__main__":
    main()