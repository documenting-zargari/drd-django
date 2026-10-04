from django.test import SimpleTestCase

from data.origin_languages import (
    classify,
    extract_origin_languages,
    normalize_level,
    sample_language_levels,
)


class NormalizeLevelTests(SimpleTestCase):
    def test_canonical_casing(self):
        self.assertEqual(normalize_level("inherited"), "Inherited")
        self.assertEqual(normalize_level(" current-l2 "), "Current-L2")

    def test_unknown_and_empty(self):
        self.assertEqual(normalize_level("Borrowed"), "Borrowed")
        self.assertIsNone(normalize_level(""))
        self.assertIsNone(normalize_level(None))


class ExtractOriginLanguagesTests(SimpleTestCase):
    def test_flat_source_language(self):
        answer = {"source": "Current-L2", "language": "Bulgarian"}
        self.assertEqual(extract_origin_languages(answer), [("language", "Current-L2", "Bulgarian")])

    def test_origin_object_and_string(self):
        self.assertEqual(
            extract_origin_languages({"origin": {"source": "Old-L2", "language": "Greek"}}),
            [("origin", "Old-L2", "Greek")],
        )
        self.assertEqual(
            extract_origin_languages({"origin": "Current-L2: Finnish"}),
            [("origin", "Current-L2", "Finnish")],
        )

    def test_nested_base_and_markers(self):
        answer = {
            "base_origin": {"source": "Recent-L2", "language": "Turkish"},
            "markers": [
                {"marker": "de", "origin": {"source": "Inherited", "language": None}},
                {"marker": "vary", "origin": {"source": "Current-L2", "language": "Romanian"}},
            ],
        }
        self.assertEqual(
            extract_origin_languages(answer),
            [("base_origin", "Recent-L2", "Turkish"), ("markers[1].origin", "Current-L2", "Romanian")],
        )

    def test_no_language_yields_nothing(self):
        self.assertEqual(extract_origin_languages({"source": "Inherited", "origin": "Inherited"}), [])
        self.assertEqual(extract_origin_languages({"language": "  ", "markers": None}), [])


class ClassifyTests(SimpleTestCase):
    levels = sample_language_levels([
        {"language": "Bulgarian", "source": "Current-L2"},
        {"language": "Turkish", "source": "Recent-L2"},
    ])

    def test_ok_is_case_insensitive(self):
        self.assertEqual(classify("bulgarian", "Current-L2", self.levels), "ok")

    def test_level_mismatch(self):
        self.assertEqual(classify("Turkish", "Current-L2", self.levels), "level_mismatch")

    def test_not_in_sample(self):
        self.assertEqual(classify("Russian", "Current-L2", self.levels), "not_in_sample")

    def test_sample_without_contact_languages(self):
        self.assertEqual(classify("Russian", "Current-L2", {}), "sample_has_no_contact_languages")
