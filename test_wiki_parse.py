"""Unit tests for wiki_parse.py (hand-written records in the real dump layout)."""

import bz2
import os
import tempfile
import unittest

from wiki_parse import (binomial_key, genus_key, iter_pages, latin_key, parse_record, plain,
                        taxobox_fields, template_end, wiki_to_text, wikitext)

ABIES = """  <page>
    <title>Abies alba</title>
    <ns>0</ns>
    <id>1234</id>
    <revision>
      <id>99</id>
      <text bytes="2000" xml:space="preserve">{{Short description|Species of conifer}}
{{Use dmy dates|date=June 2020}}
{{Speciesbox
| image = Abies alba, Hohe Wanne.jpg
| image_caption = At [[Hohe Wanne]]
| status = LC
| status_system = IUCN3.1
| status_ref = &lt;ref&gt;{{cite iucn|author=Gardner, M.|year=2013}}&lt;/ref&gt;
| genus = Abies
| species = alba
| authority = [[Philip Miller|Mill.]]
| synonyms = ''Abies pectinata''
}}
'''''Abies alba''''', the '''European silver fir''' or '''silver fir''', is a fir native to the mountains of Europe, from the [[Pyrenees]] to [[Italy]].&lt;ref name="a"&gt;Farjon 2010&lt;/ref&gt; It is a large evergreen [[coniferous]] tree.

[[File:Abies alba cones.jpg|thumb|Cones of a [[fir]] tree]]
== Description ==
It grows to {{convert|40|m|ft}} tall.
== Uses ==
Timber and [[Christmas tree]]s.
[[Category:Abies|alba]]
[[Category:Trees of Europe]]
[[Category:Medicinal plants]]
</text>
    </revision>
  </page>"""

OKRA_AUTOMATIC = """<page>
<title>Okra</title>
<ns>0</ns>
<id>77</id>
<revision><text bytes="10" xml:space="preserve">{{Automatic taxobox
| image = Okra.jpg
| taxon = Abelmoschus esculentus
| authority = ([[Carl Linnaeus|L.]]) [[Moench]]
}}
'''Okra''' or '''lady's fingers''' (''Abelmoschus esculentus'') is a flowering plant in the [[mallow]] family.
</text></revision></page>"""

OLD_TAXOBOX = """<page><title>Trifolium repens</title><ns>0</ns><id>5</id><revision>
<text xml:space="preserve">{{Taxobox
| name = White clover
| regnum = [[Plant]]ae
| genus = ''[[Trifolium]]''
| species = ''T. repens''
| binomial = ''Trifolium repens''
}}
'''White clover''' (''Trifolium repens'') is a herbaceous perennial plant in the bean family.
</text></revision></page>"""

REDIRECT = """<page><title>Abelmoschus esculentus</title><ns>0</ns><id>8</id>
<redirect title="Okra#Cultivation" />
<revision><text xml:space="preserve">#REDIRECT [[Okra]]</text></revision></page>"""

PLAIN_ARTICLE = """<page><title>Paris</title><ns>0</ns><id>9</id><revision>
<text xml:space="preserve">'''Paris''' is the capital of France.</text></revision></page>"""

TALK = """<page><title>Talk:Abies alba</title><ns>1</ns><id>10</id><revision>
<text xml:space="preserve">{{Speciesbox|genus=Abies|species=alba}}</text></revision></page>"""

INLINE = """<page><title>Inline plant</title><ns>0</ns><id>11</id><revision>
<text xml:space="preserve">{{Speciesbox | genus = Rubus | species = idaeus | authority = L.}}
'''Raspberry''' is a plant that grows in cool places and bears red edible fruit.</text></revision></page>"""


class ParseRecordTests(unittest.TestCase):
    def test_speciesbox_fields(self):
        page = parse_record(ABIES)
        self.assertEqual(page["kind"], "taxon")
        self.assertEqual(page["title"], "Abies alba")
        self.assertEqual(page["wiki_id"], "1234")
        self.assertEqual(page["taxon"], "Abies alba")
        self.assertEqual(page["genus"], "Abies")
        self.assertEqual(page["status"], "LC")
        self.assertEqual(page["authority"], "Mill.")
        self.assertEqual(page["image"], "Abies alba, Hohe Wanne.jpg")

    def test_common_names_exclude_scientific_name(self):
        page = parse_record(ABIES)
        self.assertEqual(page["common_names"], ["European silver fir", "silver fir"])

    def test_common_name_with_apostrophe(self):
        self.assertEqual(parse_record(OKRA_AUTOMATIC)["common_names"], ["lady's fingers"])

    def test_lead_is_clean_text(self):
        lead = parse_record(ABIES)["lead"]
        self.assertTrue(lead.startswith("Abies alba, the European silver fir or silver fir, is a fir"))
        for junk in ("[[", "{{", "<ref", "'''", "Farjon"):
            self.assertNotIn(junk, lead)
        self.assertIn("from the Pyrenees to Italy", lead)

    def test_categories_and_sections(self):
        page = parse_record(ABIES)
        self.assertEqual(page["categories"], ["Abies", "Trees of Europe", "Medicinal plants"])
        self.assertEqual(page["sections"], ["Description", "Uses"])

    def test_automatic_taxobox_uses_taxon_field(self):
        page = parse_record(OKRA_AUTOMATIC)
        self.assertEqual(page["title"], "Okra")
        self.assertEqual(page["taxon"], "Abelmoschus esculentus")
        self.assertEqual(page["authority"], "(L.) Moench")

    def test_old_taxobox_prefers_binomial(self):
        page = parse_record(OLD_TAXOBOX)
        self.assertEqual(page["taxon"], "Trifolium repens")
        self.assertEqual(page["genus"], "Trifolium")

    def test_single_line_taxobox(self):
        page = parse_record(INLINE)
        self.assertEqual(page["taxon"], "Rubus idaeus")
        self.assertEqual(page["authority"], "L.")

    def test_redirect_target_without_section(self):
        self.assertEqual(parse_record(REDIRECT),
                         {"kind": "redirect", "title": "Abelmoschus esculentus", "target": "Okra"})

    def test_article_without_taxobox(self):
        self.assertEqual(parse_record(PLAIN_ARTICLE), {"kind": "article", "title": "Paris"})

    def test_other_namespace_is_ignored(self):
        self.assertIsNone(parse_record(TALK))

    def test_non_page_record_is_ignored(self):
        self.assertIsNone(parse_record("</mediawiki>"))


class DumpReadingTests(unittest.TestCase):
    HEADER = '<mediawiki xmlns="x">\n  <siteinfo>\n    <sitename>Wikipedia</sitename>\n  </siteinfo>\n'

    def dump_text(self):
        return self.HEADER + ABIES.strip() + "\n" + OKRA_AUTOMATIC + "\n" + REDIRECT + "\n</mediawiki>\n"

    def test_iter_pages_plain_and_bz2_with_small_chunks(self):
        expected = ["Abies alba", "Okra", "Abelmoschus esculentus"]
        with tempfile.TemporaryDirectory() as folder:
            plain_path = os.path.join(folder, "dump.xml")
            packed_path = os.path.join(folder, "dump.xml.bz2")
            with open(plain_path, "w", encoding="utf-8") as f:
                f.write(self.dump_text())
            with open(packed_path, "wb") as f:
                f.write(bz2.compress(self.dump_text().encode("utf-8")))
            for path in (plain_path, packed_path):
                for chunk in (7, 64, 10_000_000):          # </page> may be cut by a chunk border
                    records = list(iter_pages(path, chunk_chars=chunk))
                    pages = [r for r in records if "<title>" in r]
                    self.assertEqual([parse_record(r)["title"] for r in pages], expected, (path, chunk))

    def test_first_record_with_siteinfo_is_parsed(self):
        first = next(iter_pages_from_text(self.dump_text()))
        self.assertEqual(parse_record(first)["title"], "Abies alba")

    def test_wikitext_of_deleted_page_is_none(self):
        deleted = '<page><title>X</title><ns>0</ns><id>1</id><revision><text bytes="0" xml:space="preserve" /></revision></page>'
        self.assertIsNone(wikitext(deleted))
        self.assertIsNone(parse_record(deleted))


def iter_pages_from_text(text):
    with tempfile.TemporaryDirectory() as folder:
        path = os.path.join(folder, "d.xml")
        with open(path, "w", encoding="utf-8") as f:
            f.write(text)
        yield from list(iter_pages(path))


class HelperTests(unittest.TestCase):
    def test_template_end_counts_nested_braces(self):
        text = "x {{A|{{B|c}}|d}} y {{E}}"
        start = text.index("{{A")
        self.assertEqual(text[start:template_end(text, start)], "{{A|{{B|c}}|d}}")

    def test_template_end_unbalanced(self):
        self.assertEqual(template_end("{{A|{{B}}", 0), -1)

    def test_taxobox_fields_first_key_wins(self):
        fields = taxobox_fields("{{Speciesbox\n| genus = A\n| species = b\n| genus = Z\n}}")
        self.assertEqual(fields["genus"], "A")

    def test_wiki_to_text_keeps_convert_and_lang(self):
        self.assertEqual(plain("{{convert|3|m|ft}} tall, {{lang|la|alba}} [[a|b]]"), "3 m tall, alba b")

    def test_convert_range(self):
        self.assertEqual(plain("grows to {{convert|13|-|20|ft|m|abbr=on}} tall"), "grows to 13–20 ft tall")

    def test_pronunciation_leftovers_removed(self):
        self.assertEqual(plain("Abacá ({{IPAc-en|a}}; {{respell|x}}), also known"), "Abacá, also known")
        self.assertEqual(plain("Acacia s.l. ({{pron|x}} or {{pron|y}}), known commonly"),
                         "Acacia s.l., known commonly")
        self.assertEqual(plain("Ailanthus ({{IPAc-en|x}}; derived from ailanto, a word) is a genus"),
                         "Ailanthus (derived from ailanto, a word) is a genus")

    def test_real_parentheses_are_kept(self):
        self.assertEqual(plain("a tree (or shrub) of Asia (see below)"), "a tree (or shrub) of Asia (see below)")

    def test_nbsp_template_is_a_space(self):
        self.assertEqual(plain("12{{nbsp}}m"), "12 m")

    def test_nested_file_link_removed(self):
        self.assertEqual(plain("x [[File:a.jpg|thumb|cap [[link]] end]] y"), "x y")

    def test_comment_ref_and_table_removed(self):
        text = "a<!-- hidden -->b<ref>c</ref>{| class=x\n|-\n| cell\n|}d"
        self.assertEqual(wiki_to_text(text), "abd")


class NameKeyTests(unittest.TestCase):
    def test_hybrid_sign_and_case(self):
        self.assertEqual(latin_key("Colutea × media"), "colutea x media")
        self.assertEqual(latin_key("Colutea x media"), "colutea x media")

    def test_intergeneric_hybrid_prefix_dropped(self):
        self.assertEqual(latin_key("× Cupressocyparis leylandii"), "cupressocyparis leylandii")

    def test_cultivar_name_dropped(self):
        self.assertEqual(latin_key("Acer pseudoplatanus 'Brilliantissimum'"), "acer pseudoplatanus")

    def test_binomial_and_genus(self):
        self.assertEqual(binomial_key("Abies alba pendula"), "abies alba")
        self.assertEqual(binomial_key("Colutea x media 'Copper Beauty'"), "colutea x media")
        self.assertEqual(binomial_key("Abies"), "abies")
        self.assertEqual(genus_key("Abies alba"), "abies")


if __name__ == "__main__":
    unittest.main()