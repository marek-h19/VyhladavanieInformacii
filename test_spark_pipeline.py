"""
Integration test of the Spark jobs on a tiny synthetic data set (skipped without pyspark).

  spark_extract.py  must give the same plants_full.tsv as extract_all.py
  spark_wiki.py     reads a bz2 dump, keeps taxobox articles/redirects of PFAF genera
  spark_join.py     joins plants and articles (taxobox / redirect / binomial / unmatched)

Run:  python -m unittest test_spark_pipeline -v      (starts Spark, takes about a minute)
"""

import bz2
import contextlib
import csv
import io
import json
import os
import tempfile
import unittest

try:
    import pyspark  # noqa: F401
    HAVE_SPARK, SPARK_ERROR = True, ""
except ImportError as error:                 # keep the real reason for the skip message
    HAVE_SPARK, SPARK_ERROR = False, f"{type(error).__name__}: {error}"

import extract_all
from extractor import FIELDNAMES

PLANT_HTML = (
    '<html><span id="ContentPlaceHolder1_lbldisplatinname">{display}</span>'
    '<span id="ContentPlaceHolder1_lblCommanName">{common}</span>'
    '<span id="ContentPlaceHolder1_lblFamily">{family}</span>'
    '<span id="ContentPlaceHolder1_txtSummary">{summary}</span>'
    '<span id="ContentPlaceHolder1_txtEdrating">(3 of 5)</span></html>')

# (file name, Latin name in pages.tsv or None for a legacy file, page display name, common, family)
PLANT_FILES = [
    ("Abies_alba.html", "Abies alba", "Abies alba", "Silver fir", "Pinaceae"),
    ("Abelmoschus_esculentus.html", "Abelmoschus esculentus", "Abelmoschus esculentus", "Okra", "Malvaceae"),
    ("Abelmoschus_esculentus_old.html", "Abelmoschus esculentus", "Abelmoschus esculentus", "Okra", "Malvaceae"),
    ("Allium_sativum.html", "Allium sativum", "Allium sativum", "Garlic", "Alliaceae"),
    ("Rubus_idaeus_L..html", None, "Rubus idaeus - L.", "Raspberry", "Rosaceae"),   # legacy file
    ("Abies_alba_pendula.html", "Abies alba pendula", "Abies alba pendula", "Weeping fir", "Pinaceae"),
    ("Zzz_nonexistent.html", "Zzz nonexistent", "Zzz nonexistent", "Nothing", "Zzzaceae"),
]

PAGE = ("  <page>\n    <title>{title}</title>\n    <ns>{ns}</ns>\n    <id>{id}</id>\n"
        "{redirect}    <revision>\n      <text bytes=\"1\" xml:space=\"preserve\">{text}</text>\n"
        "    </revision>\n  </page>\n")


def wiki_page(title, text, id, ns=0, redirect=None):
    tag = f'    <redirect title="{redirect}" />\n' if redirect else ""
    return PAGE.format(title=title, ns=ns, id=id, redirect=tag,
                       text=text.replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;"))


DUMP_PAGES = [
    wiki_page("Abies alba", "{{Speciesbox\n| genus = Abies\n| species = alba\n| status = LC\n"
              "| authority = [[Philip Miller|Mill.]]\n}}\n'''''Abies alba''''', the '''European silver fir''', "
              "is a fir native to the mountains of Europe and grows to a considerable height.\n"
              "[[Category:Trees of Europe]]", 1),
    wiki_page("Okra", "{{Speciesbox\n| genus = Abelmoschus\n| species = esculentus\n}}\n"
              "'''Okra''' (''Abelmoschus esculentus'') is a flowering plant in the mallow family, grown for pods.", 2),
    wiki_page("Abelmoschus esculentus", "#REDIRECT [[Okra]]", 3, redirect="Okra"),
    wiki_page("Garlic", "{{Automatic taxobox\n| taxon = Allium sativum subsp. sativum\n}}\n"
              "'''Garlic''' (''Allium sativum'') is a species of bulbous flowering plant in the genus Allium.", 4),
    wiki_page("Allium sativum", "#REDIRECT [[Garlic]]", 5, redirect="Garlic"),
    wiki_page("Raspberry", "{{Speciesbox\n| genus = Rubus\n| species = idaeus\n}}\n"
              "'''Raspberry''' is the edible fruit of a number of plant species in the genus Rubus.", 6),
    wiki_page("Panthera leo", "{{Speciesbox\n| genus = Panthera\n| species = leo\n}}\n"
              "The '''lion''' is a large cat of the genus Panthera native to Africa and India.", 7),   # not a PFAF genus
    wiki_page("Silver fir", "#REDIRECT [[Abies alba]]", 8, redirect="Abies alba"),                    # not a Latin title
    wiki_page("Abies fraseri", "#REDIRECT [[Fraser fir]]", 9, redirect="Fraser fir"),                  # target not kept
    wiki_page("Paris", "'''Paris''' is the capital of France and its largest city.", 10),
    wiki_page("Talk:Abies alba", "{{Speciesbox|genus=Abies|species=alba}}", 11, ns=1),
]
DUMP = ('<mediawiki xmlns="http://www.mediawiki.org/xml/export-0.11/">\n  <siteinfo>\n'
        '    <sitename>Wikipedia</sitename>\n  </siteinfo>\n' + "".join(DUMP_PAGES) + "</mediawiki>\n")


def read_rows(path):
    with open(path, encoding="utf-8", newline="") as f:
        return list(csv.DictReader(f, delimiter="\t"))


@unittest.skipUnless(HAVE_SPARK, f"pyspark cannot be imported ({SPARK_ERROR})")
class SparkPipelineTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        import spark_extract
        import spark_join
        import spark_wiki
        cls.tmp = tempfile.TemporaryDirectory()
        root = cls.tmp.name
        cls.data = os.path.join(root, "data")
        raw = os.path.join(cls.data, "raw_html")
        os.makedirs(raw)
        log = ["kind\turl\tfile\tstatus\tnew_plants"]
        for filename, latin, display, common, family in PLANT_FILES:
            with open(os.path.join(raw, filename), "w", encoding="utf-8") as f:
                f.write(PLANT_HTML.format(display=display, common=common, family=family,
                                          summary=f"{common} is an edible plant."))
            if latin:
                log.append(f"plant\thttps://pfaf.org/user/Plant.aspx?LatinName={latin}\traw_html/{filename}\t200\t0")
        with open(os.path.join(cls.data, "pages.tsv"), "w", encoding="utf-8", newline="") as f:
            f.write("\n".join(log) + "\n")
        cls.dump = os.path.join(root, "dump.xml.bz2")
        with open(cls.dump, "wb") as f:
            f.write(bz2.compress(DUMP.encode("utf-8")))

        quiet = contextlib.redirect_stdout(io.StringIO())
        with quiet:
            cls.plants_path = os.path.join(cls.data, "plants_full_spark.tsv")
            spark_extract.main(["--data", cls.data, "--master", "local[2]"])
            cls.wiki_dir = os.path.join(cls.data, "wiki")
            spark_wiki.main([cls.dump, "--plants", cls.plants_path, "--out", cls.wiki_dir,
                             "--master", "local[2]", "--reader", "python"])
            # the same pages split into two dump parts (one task per file)
            parts = os.path.join(root, "parts")
            os.makedirs(parts)
            for number, chunk in enumerate((DUMP_PAGES[:5], DUMP_PAGES[5:])):
                text = DUMP.split("  <page>")[0] + "".join(chunk) + "</mediawiki>\n"
                with open(os.path.join(parts, f"part{number}.xml.bz2"), "wb") as f:
                    f.write(bz2.compress(text.encode("utf-8")))
            cls.wiki_parts = os.path.join(root, "wiki_parts")
            spark_wiki.main([parts, "--plants", cls.plants_path, "--out", cls.wiki_parts,
                             "--master", "local[2]", "--reader", "python"])
            cls.wiki_hadoop = os.path.join(root, "wiki_hadoop")
            if os.name != "nt":          # Hadoop's file system needs winutils on Windows
                spark_wiki.main([cls.dump, "--plants", cls.plants_path, "--out", cls.wiki_hadoop,
                                 "--master", "local[2]", "--reader", "hadoop"])
            cls.joined_dir = os.path.join(cls.data, "joined")
            spark_join.main(["--plants", cls.plants_path, "--wiki", cls.wiki_dir,
                             "--out", cls.joined_dir, "--master", "local[2]"])
        cls.joined = {r["latin_name"]: r for r in read_rows(os.path.join(cls.joined_dir, "plants_wiki.tsv"))}

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    # ------------------------------------------------------------ extraction
    def test_spark_extract_equals_extract_all(self):
        saved = extract_all.DATA_DIR, extract_all.OUT_PATH
        try:
            extract_all.DATA_DIR = self.data
            extract_all.OUT_PATH = os.path.join(self.data, "plants_full_local.tsv")
            with contextlib.redirect_stdout(io.StringIO()):
                extract_all.main()
        finally:
            extract_all.DATA_DIR, extract_all.OUT_PATH = saved
        expected = read_rows(os.path.join(self.data, "plants_full_local.tsv"))
        actual = read_rows(self.plants_path)
        self.assertEqual(actual, expected)
        self.assertEqual(len(actual), 6)                       # 7 files, 1 duplicate
        self.assertEqual(list(actual[0].keys()), FIELDNAMES)

    def test_legacy_file_name_resolved(self):
        self.assertIn("Rubus idaeus", self.joined)

    # ------------------------------------------------------------ dump
    def test_dump_statistics(self):
        with open(os.path.join(self.wiki_dir, "dump_stats.json"), encoding="utf-8") as f:
            stats = json.load(f)
        self.assertEqual(stats["pages"], len(DUMP_PAGES))
        self.assertEqual(stats["ns0_taxon"], 5)
        self.assertEqual(stats["ns0_redirect"], 4)
        self.assertEqual(stats["ns0_article"], 1)
        self.assertEqual(stats["kept_taxon"], 4)               # Panthera is not a PFAF genus
        self.assertEqual(stats["kept_redirect"], 3)            # "Silver fir" has no PFAF genus

    def same_wiki_output(self, other_dir):
        for name in ("wiki_taxa.tsv", "wiki_redirects.tsv"):
            self.assertEqual(read_rows(os.path.join(other_dir, name)),
                             read_rows(os.path.join(self.wiki_dir, name)), name)
        counts = []
        for folder in (other_dir, self.wiki_dir):
            with open(os.path.join(folder, "dump_stats.json"), encoding="utf-8") as f:
                stats = json.load(f)
            counts.append({k: v for k, v in stats.items() if k not in ("seconds", "dump_mb")})
        self.assertEqual(counts[0], counts[1])

    def test_dump_split_into_parts_gives_same_result(self):
        self.same_wiki_output(self.wiki_parts)

    @unittest.skipIf(os.name == "nt", "the hadoop reader needs winutils on Windows")
    def test_hadoop_reader_gives_same_result(self):
        self.same_wiki_output(self.wiki_hadoop)

    def test_kept_articles_and_redirects(self):
        titles = {r["title"] for r in read_rows(os.path.join(self.wiki_dir, "wiki_taxa.tsv"))}
        self.assertEqual(titles, {"Abies alba", "Okra", "Garlic", "Raspberry"})
        redirects = {r["title"]: r["target"]
                     for r in read_rows(os.path.join(self.wiki_dir, "wiki_redirects.tsv"))}
        self.assertEqual(redirects["Abelmoschus esculentus"], "Okra")
        self.assertNotIn("Silver fir", redirects)

    # ------------------------------------------------------------ join
    def test_taxobox_match(self):
        row = self.joined["Abies alba"]
        self.assertEqual((row["wiki_title"], row["wiki_match"]), ("Abies alba", "taxobox"))
        self.assertEqual(row["wiki_status"], "LC")
        self.assertEqual(row["wiki_common_names"], "European silver fir")
        self.assertIn("Trees of Europe", row["wiki_categories"])

    def test_species_stored_under_common_name(self):
        row = self.joined["Abelmoschus esculentus"]
        self.assertEqual(row["wiki_title"], "Okra")
        self.assertEqual(row["wiki_match"], "taxobox")         # taxobox beats the redirect

    def test_redirect_match(self):
        row = self.joined["Allium sativum"]
        self.assertEqual((row["wiki_title"], row["wiki_match"]), ("Garlic", "redirect"))

    def test_binomial_match_for_cultivar_or_variety(self):
        row = self.joined["Abies alba pendula"]
        self.assertEqual((row["wiki_title"], row["wiki_match"]), ("Abies alba", "binomial"))

    def test_unmatched_plant_kept_with_empty_wiki_columns(self):
        row = self.joined["Zzz nonexistent"]
        self.assertEqual(row["wiki_title"], "")
        with open(os.path.join(self.joined_dir, "unmatched.txt"), encoding="utf-8") as f:
            self.assertEqual(f.read().split(), ["Zzz", "nonexistent"])

    def test_unique_pages_less_than_joined_plants(self):
        matched = [r for r in self.joined.values() if r["wiki_title"]]
        self.assertEqual(len(matched), 5)
        self.assertEqual(len({r["wiki_title"] for r in matched}), 4)   # Abies alba used twice


if __name__ == "__main__":
    unittest.main()
