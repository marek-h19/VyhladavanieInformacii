import csv
import math
import os
import tempfile
import unittest

import indexer
from indexer import Index, build_index, idf_classic, idf_max, idf_smooth, tokenize

CORPUS = [
    # "plant" occurs in every document: df = N = max_df (needed for a sensible idf max)
    ("Malus domestica", "Apple", "Rosaceae", "plant edible fruit tree"),
    ("Rubus idaeus", "Raspberry", "Rosaceae", "plant edible leaves shade"),
    ("Salvia officinalis", "Sage", "Lamiaceae", "plant medicinal leaves"),
    ("Ribes nigrum", "Blackcurrant", "Grossulariaceae", "plant medicinal fruit shade tolerant"),
]


def write_corpus(path):
    fields = ["latin_name", "common_name", "family", "summary"]
    with open(path, "w", encoding="utf-8", newline="") as f:
        writer = csv.writer(f, delimiter="\t")
        writer.writerow(fields)
        writer.writerows(CORPUS)


class TokenizerTests(unittest.TestCase):
    def test_lowercase_and_split(self):
        self.assertEqual(tokenize("Moist, Shady-ground; 2 m."), ["moist", "shady", "ground"])

    def test_stopwords_and_single_letters_removed(self):
        self.assertEqual(tokenize("The leaves of a tree"), ["leaves", "tree"])

    def test_unicode_letters_are_kept(self):
        self.assertEqual(tokenize("Kopřiva dvoudomá"), ["kopřiva", "dvoudomá"])


class IdfFormulaTests(unittest.TestCase):
    def test_classic(self):
        self.assertAlmostEqual(idf_classic(100, 10, 50), 1.0)
        self.assertEqual(idf_classic(100, 100, 100), 0.0)

    def test_smooth_is_always_at_least_one(self):
        self.assertAlmostEqual(idf_smooth(99, 9, 50), math.log(10) + 1)
        self.assertGreaterEqual(idf_smooth(100, 100, 100), 1.0)

    def test_max_is_never_negative(self):
        self.assertAlmostEqual(idf_max(100, 9, 49), math.log(49 / 10))
        self.assertEqual(idf_max(100, 50, 50), 0.0)

    def test_rarer_terms_get_higher_idf_in_all_methods(self):
        for fn in (idf_classic, idf_smooth, idf_max):
            self.assertGreater(fn(1000, 2, 400), fn(1000, 200, 400))


class RankingTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = tempfile.TemporaryDirectory()
        plants = os.path.join(cls.tmp.name, "plants_full.tsv")
        write_corpus(plants)
        cls.meta = build_index(plants, os.path.join(cls.tmp.name, "index"))
        cls.index = Index(os.path.join(cls.tmp.name, "index"))

    @classmethod
    def tearDownClass(cls):
        cls.tmp.cleanup()

    def names(self, query, method):
        return [self.index.docs[d]["latin_name"] for d, _ in self.index.search(query, method)]

    def test_meta(self):
        self.assertEqual(self.meta["documents"], 4)
        self.assertEqual(self.index.n_docs, 4)

    def test_document_with_both_terms_ranks_first(self):
        for method in indexer.IDF_METHODS:
            self.assertEqual(self.names("fruit shade", method)[0], "Ribes nigrum")

    def test_name_match_beats_body_match(self):
        self.assertEqual(self.names("sage", "smooth")[0], "Salvia officinalis")

    def test_unknown_term_gives_no_results(self):
        self.assertEqual(self.names("zzzunknown", "classic"), [])

    def test_term_in_all_documents_is_useless_for_classic_idf(self):
        everywhere = [t for t in self.index.postings if self.index.df(t) == 4]
        for term in everywhere:
            self.assertEqual(self.index.search(term, "classic"), [])

    def test_scores_are_sorted_descending(self):
        scores = [s for _, s in self.index.search("edible leaves fruit", "smooth")]
        self.assertEqual(scores, sorted(scores, reverse=True))

    def test_k_limits_results(self):
        self.assertEqual(len(self.index.search("fruit leaves", "smooth", k=2)), 2)

    def test_reference_markers_are_not_indexed(self):
        self.assertEqual(self.index.df("235"), 0)


if __name__ == "__main__":
    unittest.main()