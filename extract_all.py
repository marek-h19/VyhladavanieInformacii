
import csv
import glob
import os
import re
from urllib.parse import parse_qsl, urlsplit

from extractor import FIELDNAMES, extract_by_id, extract_row


DATA_DIR = "data"
OUT_PATH = os.path.join(DATA_DIR, "plants_full.tsv")


def names_from_pages_log():
    """file name -> Latin name, taken from the URL logged by the crawler."""
    mapping = {}
    path = os.path.join(DATA_DIR, "pages.tsv")
    if not os.path.exists(path):
        return mapping
    with open(path, encoding="utf-8", newline="") as f:
        for row in csv.DictReader(f, delimiter="\t"):
            if row["kind"] == "plant" and row["file"]:
                query = {k.lower(): v for k, v in parse_qsl(urlsplit(row["url"]).query)}
                name = re.sub(r"\s+", " ", query.get("latinname", "")).strip()
                mapping[os.path.basename(row["file"])] = name
    return mapping


def main():
    mapping = names_from_pages_log()
    files = sorted(glob.glob(os.path.join(DATA_DIR, "raw_html", "*.html")))
    rows, duplicates = {}, 0
    for path in files:
        base = os.path.basename(path)
        with open(path, encoding="utf-8", errors="replace") as f:
            text = f.read()
        name = mapping.get(base)
        if not name:                           # legacy file: not in pages.tsv
            display = extract_by_id("lbldisplatinname", text)
            name = (display.split(" - ")[0].strip() if display
                    else os.path.splitext(base)[0].replace("_", " "))
        key = name.lower()
        if key in rows:
            duplicates += 1                    # same plant saved twice (old + new crawler)
            continue
        rows[key] = extract_row(name, text)

    with open(OUT_PATH, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, FIELDNAMES, delimiter="\t")
        writer.writeheader()
        for key in sorted(rows):
            writer.writerow(rows[key])

    total = len(rows)
    print(f"files read: {len(files)}, unique plants: {total}, duplicates skipped: {duplicates}")
    print(f"written: {OUT_PATH}\n")
    print(f"{'field':26} {'filled':>7} {'percent':>8}")
    for field in FIELDNAMES[1:]:
        filled = sum(1 for row in rows.values() if row[field] != "")
        print(f"{field:26} {filled:>7} {100 * filled / max(total, 1):>8.2f}%")

    empty = sorted(row["latin_name"] for row in rows.values() if not row["family"])
    print(f"\npages with empty family (probably not real plant pages): {len(empty)}")
    for name in empty[:10]:
        print("  ", name)


if __name__ == "__main__":
    main()