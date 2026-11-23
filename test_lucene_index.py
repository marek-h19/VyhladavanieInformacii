"""
Tests of the Lucene index on six hand-written plants (needs PyLucene; skipped without it).

    docker run -it --rm -v ${PWD}:/work -w /work coady/pylucene python -m unittest test_lucene_index -v
"""

import contextlib
import io
import os
import tempfile
import unittest

try:
    import lucene_index
    from lucene_index import Searcher, build_index
    LUCENE_ERROR = None
except Exception as error:                               # PyLucene / JVM not available
    LUCENE_ERROR = f"PyLucene is not available: {error}"

HEADER = ("latin_name\tcommon_name\tfamily\tusda_hardiness\tknown_hazards\tsummary\tedible_uses\t"
          "medicinal_uses\tother_uses\tcultivation\tsynonyms\tcare\tedibility_rating\t"
          "medicinal_rating\tother_uses_rating\twiki_title\twiki_match\twiki_status\t"
          "wiki_common_names\twiki_lead\twiki_categories\twiki_text_len")
ROWS = [
    "Abies alba\tSilver fir\tPinaceae\t5-8\tNone known\tA large evergreen conifer\tYoung shoots are edible"
    "\tResin used for wound healing\tTimber\tPrefers moist soil\tAbies pectinata\tMoist Soil; Full sun"
    "\t2\t3\t4\tAbies alba\ttaxobox\tLC\tEuropean silver fir; silver fir"
    "\tAbies alba is a fir native to Europe\tTrees of Europe; Medicinal plants\t12000",
    "Trifolium repens\tWhite clover\tFabaceae\t4-9\tNone known\tNitrogen fixing ground cover"
    "\tEdible flowers and leaves\tPoultice for wounds\tNitrogen fixing green manure"
    "\tSemi-shade tolerant\t\tSemi-shade; Moist Soil\t3\t2\t4\tTrifolium repens\ttaxobox\tLC"
    "\twhite clover; Dutch clover\tTrifolium repens is a herbaceous plant\tMedicinal plants; Fabaceae\t9000",
    "Robinia pseudoacacia\tBlack locust\tFabaceae\t4-8\tToxic bark\tA nitrogen fixing tree used for hedge"
    "\tFlowers are edible\tNot used\tWood fixing posts\tFull sun\t\tFull sun\t1\t0\t4"
    "\tRobinia pseudoacacia\ttaxobox\tLC\tblack locust\tRobinia is a tree"
    "\tTrees of North America; Fabaceae\t30000",
    "Rosmarinus officinalis\tRosemary\tLamiaceae\t7-10\tNone known\tAromatic shrub"
    "\tLeaves used as a herb\tUsed to heal wounds\tHedge plant\tFull sun, dry soil\tSalvia rosmarinus"
    "\tFull sun\t4\t3\t3\tRosemary\tredirect\tVU\trosemary\tSalvia rosmarinus is an aromatic shrub"
    "\tMedicinal plants; Herbs\t25000",
    "Abelmoschus esculentus\tOkra, Lady's fingers\tMalvaceae\tComing soon\tNone known\tAnnual vegetable"
    "\tThe edible fruit pods\tPoultice\tFibre\tHot climate\t\tFull sun\t5\t1\t1\t\t\t\t\t\t\t",
    "Fragaria vesca\tWild strawberry\tRosaceae\t5-9\tNone known\tSmall edible fruits tolerant of shade"
    "\tFruits are edible, shade tolerant\tLeaves tea\tGround cover\tShade ok\tFragaria vulgaris"
    "\tSemi-shade\t5\t2\t2\tFragaria vesca\ttaxobox\tLC\twild strawberry; woodland strawberry"
    "\tFragaria vesca is a strawberry\tFruits; Medicinal plants\t15000",
]


@unittest.skipIf(LUCENE_ERROR, LUCENE_ERROR or "")
class LuceneIndexTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.folder = tempfile.TemporaryDirectory()
        cls.tsv = os.path.join(cls.folder.name, "plants_wiki.tsv")
        with open(cls.tsv, "w", encoding="utf-8", newline="") as f:
            f.write(HEADER + "\n" + "\n".join(ROWS) + "\n")
        cls.index_dir = os.path.join(cls.folder.name, "index")
        cls.meta = build_index(cls.tsv, cls.index_dir)
        cls.searcher = Searcher(cls.index_dir)

    @classmethod
    def tearDownClass(cls):
        cls.searcher.close()
        cls.folder.cleanup()

    def names(self, query, **options):
        return [r["latin_name"] for r in self.searcher.search(query, 10, **options)["results"]]

    def hits(self, query, **options):
        return self.searcher.search(query, 10, **options)["hits"]

    # -- the index itself
    def test_build_counts(self):
        self.assertEqual(self.meta["documents"], 6)
        self.assertEqual(self.meta["with_wiki"], 5)
        self.assertEqual(self.searcher.num_docs, 6)

    # -- free text and Boolean queries
    def test_or_is_the_default_and_and_narrows(self):
        self.assertEqual(self.hits("edible fruit shade"), 5)          # Rosemary has none of them
        self.assertEqual(self.names("edible fruit shade", operator="and"), ["Fragaria vesca"])

    def test_boolean_operators(self):
        self.assertEqual(sorted(self.names("nitrogen fixing")), ["Robinia pseudoacacia", "Trifolium repens"])
        self.assertEqual(self.names("nitrogen fixing -tree"), ["Trifolium repens"])
        self.assertEqual(self.names("+nitrogen +hedge"), ["Robinia pseudoacacia"])
        self.assertEqual(sorted(self.names("clover OR locust")), ["Robinia pseudoacacia", "Trifolium repens"])
        self.assertEqual(self.names("(clover OR locust) AND black"), ["Robinia pseudoacacia"])

    def test_phrase_respects_word_order_and_slop(self):
        self.assertEqual(self.hits('"nitrogen fixing"'), 2)
        self.assertEqual(self.hits('"fixing nitrogen"'), 0)
        self.assertEqual(self.hits('"fixing nitrogen"~2'), 2)

    def test_stemming_in_prose_but_not_in_names(self):
        self.assertEqual(sorted(self.names("fruits")), ["Abelmoschus esculentus", "Fragaria vesca"])
        self.assertEqual(self.hits("rosemaries"), 0)       # names are not stemmed

    def test_fuzzy_finds_typos(self):
        self.assertEqual(self.hits("offcinalis"), 0)
        self.assertEqual(self.names("offcinalis", fuzzy=True), ["Rosmarinus officinalis"])
        self.assertEqual(self.names("offcinalis~1"), ["Rosmarinus officinalis"])

    def test_prefix_and_field_queries(self):
        self.assertEqual(self.names("latin_name:abies*"), ["Abies alba"])
        self.assertEqual(self.names('common_name:"black locust"'), ["Robinia pseudoacacia"])

    def test_malformed_query_does_not_raise(self):
        self.assertEqual(self.hits('unbalanced "quote'), 0)

    # -- ranking
    def test_wikipedia_common_name_is_searchable(self):
        self.assertEqual(self.names("european silver fir")[0], "Abies alba")
        self.assertEqual(self.names("dutch clover")[0], "Trifolium repens")

    def test_name_field_beats_prose_field(self):
        # "hedge" is in the summary of Robinia and in the other_uses of Rosemary; a name wins
        self.assertEqual(self.names("rosemary")[0], "Rosmarinus officinalis")

    def test_explain_shows_bm25(self):
        doc_id = self.searcher.search("silver fir", 1)["results"][0]["doc_id"]
        self.assertIn("BM25", self.searcher.explain("silver fir", doc_id))

    # -- filters and ranges
    def test_family_category_status_care_filters(self):
        self.assertEqual(sorted(self.names("family:Fabaceae")), ["Robinia pseudoacacia", "Trifolium repens"])
        self.assertEqual(self.hits('category:"medicinal plants"'), 4)
        self.assertEqual(self.names("status:vu"), ["Rosmarinus officinalis"])
        self.assertEqual(self.hits('care:"full sun"'), 4)
        self.assertEqual(self.names("wiki:no"), ["Abelmoschus esculentus"])
        self.assertEqual(self.hits("wiki:yes"), 5)

    def test_filter_does_not_change_ranking_but_narrows(self):
        plain = self.names("nitrogen fixing")
        self.assertEqual(self.names("family:fabaceae nitrogen fixing"), plain)
        self.assertEqual(self.names("family:pinaceae nitrogen fixing"), [])

    def test_rating_ranges(self):
        self.assertEqual(sorted(self.names("edibility>=4")),
                         ["Abelmoschus esculentus", "Fragaria vesca", "Rosmarinus officinalis"])
        self.assertEqual(self.names("edibility>=4 medicinal>=3"), ["Rosmarinus officinalis"])
        self.assertEqual(self.hits("edibility<=1"), 1)

    def test_hardiness_zone_range_and_missing_value(self):
        self.assertEqual(sorted(self.names("hardiness:9")),
                         ["Fragaria vesca", "Rosmarinus officinalis", "Trifolium repens"])
        self.assertEqual(self.hits("hardiness:6"), 4)          # Okra has no zone ("Coming soon")

    def test_article_length_range(self):
        self.assertEqual(sorted(self.names("wikilen>=25000")), ["Robinia pseudoacacia", "Rosmarinus officinalis"])

    def test_filters_combine_with_text(self):
        self.assertEqual(sorted(self.names('category:"medicinal plants" wound healing')),
                         ["Abies alba", "Rosmarinus officinalis", "Trifolium repens"])
        self.assertEqual(self.names("hardiness:6 edibility>=3 shade"), ["Fragaria vesca", "Trifolium repens"])


@unittest.skipIf(LUCENE_ERROR, LUCENE_ERROR or "")
class CompareQueriesTests(unittest.TestCase):
    def test_comparison_report_runs_on_both_indexes(self):
        import compare_queries
        import indexer
        with tempfile.TemporaryDirectory() as folder:
            tsv = os.path.join(folder, "plants_wiki.tsv")
            with open(tsv, "w", encoding="utf-8", newline="") as f:
                f.write(HEADER + "\n" + "\n".join(ROWS) + "\n")
            old_dir, new_dir = os.path.join(folder, "old"), os.path.join(folder, "new")
            indexer.build_index(tsv, old_dir)
            build_index(tsv, new_dir)
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                compare_queries.main(["--old-index", old_dir, "--new-index", new_dir, "-k", "3"])
        text = out.getvalue()
        self.assertIn("===== Summary =====", text)
        self.assertIn("Phrase", text)
        self.assertIn("cannot express this query", text)


if __name__ == "__main__":
    unittest.main()
