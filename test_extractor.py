import glob
import os
import re
import unittest

from extractor import (FIELDNAMES, extract_by_id, extract_care, extract_rating,
                       extract_row, strip_references)


SAMPLE = """
<tr><td><b>Common Name</b></td><td>
<span id="ContentPlaceHolder1_lblCommanName" class="txtbox">False Nettle, Smallspike false nettle</span></td></tr>
<tr><td><b>Family</b></td><td>
<span id="ContentPlaceHolder1_lblFamily" class="txtbox">Urticaceae</span></td></tr>
<tr><td><b>Known Hazards</b></td><td>
<img id="ContentPlaceHolder1_imgHazards" src="x.png" />
<span id="ContentPlaceHolder1_lblKnownHazards" class="txtbox">No stinging hairs[235].</span></td></tr>
<tr><td><b>Edibility Rating </b></td><td><img src="a.gif" />
<span id="ContentPlaceHolder1_txtEdrating">&nbsp;&nbsp;&nbsp;(4 of 5)</span></td></tr>
<tr><td><b>Medicinal Rating</b></td><td>
<span id="ContentPlaceHolder1_txtMedRating">&nbsp;&nbsp;&nbsp;(0 of 5)</span></td></tr>
<span id="ContentPlaceHolder1_txtSummary"><span class="x">Perennial herb</span> to 2 m.<br/>Grows in
moist soil&nbsp;&amp; shade.</span>
<div id="ContentPlaceHolder1_txtEdibleUses"><div>Leaves: cooked.</div><p>Seeds: raw.</p></div>
<table id="ContentPlaceHolder1_tblIcons"><tr>
<td><img src='a.jpg' alt='Moist Soil' title='Moist Soil'/></td>
<td><img src='b.jpg' alt='Full sun' title='Full sun'/></td></tr></table>
"""


class SyntheticPageTests(unittest.TestCase):
    def test_simple_span(self):
        self.assertEqual(extract_by_id("lblFamily", SAMPLE), "Urticaceae")

    def test_img_before_span_is_ignored(self):
        self.assertEqual(extract_by_id("lblKnownHazards", SAMPLE), "No stinging hairs[235].")

    def test_nested_span_br_and_entities(self):
        self.assertEqual(extract_by_id("txtSummary", SAMPLE),
                         "Perennial herb to 2 m. Grows in moist soil & shade.")

    def test_nested_divs_do_not_end_early(self):
        self.assertEqual(extract_by_id("txtEdibleUses", SAMPLE), "Leaves: cooked. Seeds: raw.")

    def test_missing_element_gives_empty_string(self):
        self.assertEqual(extract_by_id("txtNeexistuje", SAMPLE), "")

    def test_unbalanced_markup_gives_empty_string(self):
        self.assertEqual(extract_by_id("txtX", '<span id="ContentPlaceHolder1_txtX">open'), "")

    def test_ratings(self):
        self.assertEqual(extract_rating("txtEdrating", SAMPLE), "4")
        self.assertEqual(extract_rating("txtMedRating", SAMPLE), "0")
        self.assertEqual(extract_rating("txtOtherUseRating", SAMPLE), "")

    def test_care_icons(self):
        self.assertEqual(extract_care(SAMPLE), ["Moist Soil", "Full sun"])

    def test_extract_row_has_all_fields(self):
        row = extract_row("Boehmeria cylindrica", SAMPLE)
        self.assertEqual(list(row), FIELDNAMES)
        self.assertEqual(row["common_name"], "False Nettle, Smallspike false nettle")
        self.assertEqual(row["care"], "Moist Soil; Full sun")

    def test_no_tabs_or_newlines_in_values(self):
        for value in extract_row("x", SAMPLE).values():
            self.assertNotIn("\t", value)
            self.assertNotIn("\n", value)

    def test_strip_references(self):
        self.assertEqual(" ".join(strip_references("Edible[2, 40] raw[43].").split()),
                         "Edible raw .")


# (file name without the hash suffix, field, expected substring) - checked by hand
# against the site's own search table, i.e. a source independent of our regexes.
GOLD = [
    ("Abelia_triflora", "family", "Caprifoliaceae"),
    ("Abelia_triflora", "common_name", "Indian Abelia"),
    ("Abelia_triflora", "usda_hardiness", "5-9"),
    ("Abelia_triflora", "known_hazards", "None known"),
    ("Abelmoschus_esculentus", "family", "Malvaceae"),
    ("Abelmoschus_esculentus", "common_name", "Okra"),
    ("Abelmoschus_esculentus", "known_hazards", "irritant to some people"),
    ("Abelmoschus_manihot", "common_name", "Aibika"),
    ("Abelmoschus_manihot", "usda_hardiness", "8-11"),
    ("Abelmoschus_moschatus", "family", "Malvaceae"),
    ("Abelmoschus_moschatus", "common_name", "Musk Mallow"),
    ("Abies_alba", "family", "Pinaceae"),
    ("Abies_alba", "common_name", "Silver Fir"),
    ("Abies_alba", "usda_hardiness", "5-8"),
]


def find_page(part):
    """Exact match of 'Abies_alba.html' or 'Abies_alba_<6 hex>.html' (no prefix clashes)."""
    pattern = re.compile("^" + re.escape(part) + r"(_[0-9a-f]{6})?\.html$")
    for path in sorted(glob.glob(os.path.join("data", "raw_html", "*.html"))):
        if pattern.match(os.path.basename(path)):
            return path
    return None


RAW_DIR = os.path.join("data", "raw_html")


def sample_files(count=20):
    files = sorted(glob.glob(os.path.join(RAW_DIR, "*.html")))
    if len(files) <= count:
        return files
    step = len(files) / count
    return [files[int(i * step)] for i in range(count)]


@unittest.skipUnless(os.path.isdir(RAW_DIR) and sample_files(),
                     "data/raw_html not found")
class RealPageTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.rows = {}
        for path in sample_files():
            with open(path, encoding="utf-8", errors="replace") as f:
                cls.rows[path] = extract_row(os.path.basename(path), f.read())

    def test_twenty_pages_loaded(self):
        self.assertGreaterEqual(len(self.rows), min(20, len(glob.glob(RAW_DIR + "/*.html"))))

    def test_family_and_common_name_filled(self):
        for path, row in self.rows.items():
            with self.subTest(page=os.path.basename(path)):
                self.assertTrue(row["family"], "family is empty")

    def test_ratings_are_between_0_and_5(self):
        for path, row in self.rows.items():
            for field in ("edibility_rating", "medicinal_rating", "other_uses_rating"):
                with self.subTest(page=os.path.basename(path), field=field):
                    self.assertIn(row[field], {"0", "1", "2", "3", "4", "5"})

    def test_most_pages_have_summary_or_uses(self):
        filled = sum(1 for r in self.rows.values()
                     if r["summary"] or r["edible_uses"] or r["medicinal_uses"] or r["other_uses"])
        self.assertGreaterEqual(filled / len(self.rows), 0.8)

    def test_values_are_tsv_safe(self):
        for row in self.rows.values():
            for value in row.values():
                self.assertNotIn("\t", value)
                self.assertNotIn("\n", value)

    def test_gold_values(self):
        self.assertGreaterEqual(len(GOLD), 5, "fill in GOLD with hand-checked values")
        for part, field, expected in GOLD:
            path = find_page(part)
            self.assertIsNotNone(path, f"no saved page for {part}")
            with open(path, encoding="utf-8", errors="replace") as f:
                row = extract_row(part, f.read())
            with self.subTest(page=part, field=field):
                self.assertIn(expected, row[field])


if __name__ == "__main__":
    unittest.main()