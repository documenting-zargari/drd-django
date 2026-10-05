from django.test import SimpleTestCase

from data.origin_languages import (
    classify,
    extract_origin_languages,
    normalize_level,
    sample_language_levels,
    unify_answer,
    unify_contact_languages,
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


class SampleLanguageLevelsTests(SimpleTestCase):
    def test_reads_renamed_level_key(self):
        self.assertEqual(
            sample_language_levels([{"language": "Greek", "level": "Old-L2"}]),
            {"greek": {"Old-L2"}},
        )


class UnifyAnswerTests(SimpleTestCase):
    def test_flat_source_language(self):
        patch, problem = unify_answer({"source": "inherited", "language": ""})
        self.assertIsNone(problem)
        self.assertEqual(patch, {"source": None, "language": None, "origin": "Inherited"})

    def test_empty_source_is_dropped(self):
        self.assertEqual(unify_answer({"source": ""}), ({"source": None}, None))

    def test_origin_object_and_language_string(self):
        self.assertEqual(
            unify_answer({"origin": {"source": "Old-L2", "language": "Greek"}}),
            ({"origin": "Old-L2"}, None),
        )
        self.assertEqual(unify_answer({"origin": "Current-L2: Finnish"}), ({"origin": "Current-L2"}, None))

    def test_preposition_origin_folds_into_origin(self):
        self.assertEqual(
            unify_answer({"preposition_origin": "Current-L2"}),
            ({"preposition_origin": None, "origin": "Current-L2"}, None),
        )

    def test_nested_base_and_markers(self):
        answer = {
            "origin": {"source": "Inherited", "language": None},
            "base_origin": {"source": "Recent-L2", "language": "Turkish"},
            "markers": [
                {"marker": "de"},
                {"marker": "vary", "origin": {"source": "Current-L2", "language": "Romanian"}},
                {"marker": "x", "origin": {"source": None, "language": None}},
            ],
        }
        patch, problem = unify_answer(answer)
        self.assertIsNone(problem)
        self.assertEqual(patch, {
            "origin": "Inherited",
            "base_origin": "Recent-L2",
            "markers": [{"marker": "de"}, {"marker": "vary", "origin": "Current-L2"}, {"marker": "x"}],
        })
        self.assertEqual(answer["markers"][1]["origin"]["source"], "Current-L2")  # input untouched

    def test_already_unified_is_noop(self):
        answer = {"origin": "Inherited", "base_origin": "Old-L2", "markers": [{"marker": "a", "origin": "Inherited"}]}
        self.assertEqual(unify_answer(answer), ({}, None))
        self.assertEqual(unify_answer({"form": "x"}), ({}, None))

    def test_conflicting_levels_are_refused(self):
        patch, problem = unify_answer({"source": "Current-L2", "language": "Bulgarian", "origin": "Inherited"})
        self.assertIsNone(patch)
        self.assertIn("conflicting levels", problem)

    def test_same_level_twice_is_fine(self):
        self.assertEqual(
            unify_answer({"source": "Current-L2", "origin": "Current-L2"}),
            ({"source": None}, None),
        )

    def test_unknown_level_is_refused(self):
        patch, problem = unify_answer({"source": "Borrowed"})
        self.assertIsNone(patch)
        self.assertIn("unknown level", problem)


class UnifyContactLanguagesTests(SimpleTestCase):
    def test_renames_source_to_level(self):
        self.assertEqual(
            unify_contact_languages([{"source": "current-l2", "language": "Bulgarian"}]),
            [{"language": "Bulgarian", "level": "Current-L2"}],
        )

    def test_idempotent(self):
        unified = [{"language": "Bulgarian", "level": "Current-L2"}]
        self.assertEqual(unify_contact_languages(unified), unified)


class RewriteSpecTests(SimpleTestCase):
    def test_maps_origin_bindings(self):
        from data.management.commands.unify_origin import rewrite_spec

        spec = {"tables": [{"columns": [
            {"cell": {"field": "source|language|origin", "layout": "inline"}},
            {"cell": {"field": "markers.origin.source|markers.origin.language"}},
            {"cell": {"field": "base_origin.source|base_origin.language"}},
            {"cell": {"field": "form"}},
        ]}]}
        self.assertEqual(rewrite_spec(spec), 3)
        self.assertEqual(
            [c["cell"]["field"] for c in spec["tables"][0]["columns"]],
            ["origin", "markers.origin", "base_origin", "form"],
        )
        self.assertEqual(rewrite_spec(spec), 0)


class IsBackedUpTests(SimpleTestCase):
    def test_requires_every_language_in_backup(self):
        from data.management.commands.unify_origin import is_backed_up

        answer = {"source": "Current-L2", "language": "Bulgarian"}
        self.assertFalse(is_backed_up(answer))
        self.assertTrue(is_backed_up({**answer, "legacy_origin_language": {"language": "Bulgarian"}}))
        self.assertTrue(is_backed_up({"source": "Inherited"}))


class ContactLanguageFilterTests(SimpleTestCase):
    def test_parses_level_and_any(self):
        from data.origin_languages import parse_contact_language_filters

        self.assertEqual(
            parse_contact_language_filters(["current-l2: Russian", "any:Turkish"]),
            [{"level": "Current-L2", "language": "russian"}, {"level": None, "language": "turkish"}],
        )

    def test_drops_malformed_and_non_l2_levels(self):
        from data.origin_languages import parse_contact_language_filters

        self.assertEqual(parse_contact_language_filters(["Russian", "Inherited:Russian", "Old-L2:", None]), [])
        self.assertEqual(parse_contact_language_filters(None), [])
