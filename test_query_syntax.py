"""Unit tests for query_syntax.py (no Lucene needed)."""

import unittest

from query_syntax import MAX_INT, fuzzify, parse_filters, parse_hardiness


class ParseFiltersTests(unittest.TestCase):
    def test_text_without_filters_is_unchanged(self):
        self.assertEqual(parse_filters('"nitrogen fixing" tree'), ('"nitrogen fixing" tree', []))

    def test_family_is_lower_cased_and_removed_from_the_text(self):
        text, filters = parse_filters("family:Fabaceae nitrogen fixing")
        self.assertEqual(text, "nitrogen fixing")
        self.assertEqual(filters, [("term", "family_key", "fabaceae")])

    def test_quoted_value_with_spaces(self):
        text, filters = parse_filters('wound category:"Medicinal plants" healing')
        self.assertEqual(text, "wound healing")
        self.assertEqual(filters, [("term", "category", "medicinal plants")])

    def test_care_and_status_and_wiki(self):
        _, filters = parse_filters('care:"Full sun" status:LC wiki:no match:taxobox')
        self.assertEqual(filters, [("term", "care_key", "full sun"), ("term", "status", "lc"),
                                   ("term", "wiki_present", "0"), ("term", "wiki_match", "taxobox")])

    def test_numeric_comparisons(self):
        text, filters = parse_filters("edibility>=4 medicinal > 2 otheruses<=3 shade wikilen=100")
        self.assertEqual(text, "shade")
        self.assertEqual(filters, [("range", "edibility_rating", 4, MAX_INT),
                                   ("range", "medicinal_rating", 3, MAX_INT),
                                   ("range", "other_uses_rating", 0, 3),
                                   ("range", "wiki_text_len", 100, 100)])

    def test_hardiness_zone_is_inside_the_plants_range(self):
        text, filters = parse_filters("hardiness:7 tree")
        self.assertEqual(text, "tree")
        self.assertEqual(filters, [("range", "hardiness_min", 0, 7), ("range", "hardiness_max", 7, 99)])

    def test_field_names_inside_words_are_not_filters(self):
        text, filters = parse_filters("subfamily:x unfamily: edibility")
        self.assertEqual(filters, [])
        self.assertEqual(text, "subfamily:x unfamily: edibility")

    def test_plain_lucene_field_query_is_left_for_the_parser(self):
        self.assertEqual(parse_filters("common_name:okra latin_name:abies*"),
                         ("common_name:okra latin_name:abies*", []))


class FuzzifyTests(unittest.TestCase):
    def test_plain_words_get_one_edit(self):
        self.assertEqual(fuzzify("rosmarinus offcinalis"), "rosmarinus~1 offcinalis~1")

    def test_short_words_operators_phrases_and_syntax_stay(self):
        self.assertEqual(fuzzify('fir AND "silver fir" -tree abies* name:okra pear~2'),
                         'fir AND "silver fir" -tree abies* name:okra pear~2')

    def test_non_ascii_letters_count(self):
        self.assertEqual(fuzzify("Žltá"), "Žltá~1")


class HardinessTests(unittest.TestCase):
    def test_values(self):
        self.assertEqual(parse_hardiness("5-9"), (5, 9))
        self.assertEqual(parse_hardiness(" 7 "), (7, 7))
        self.assertEqual(parse_hardiness("10-11"), (10, 11))

    def test_not_a_zone(self):
        for value in ("Coming soon", "", None, "9-5", "59", "5-20"):
            self.assertIsNone(parse_hardiness(value), value)


if __name__ == "__main__":
    unittest.main()
