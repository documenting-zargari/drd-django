"""Unit tests for the table spec validator + legacy-HTML converter.

Pure functions - no database. Fixture ``content`` strings are trimmed copies of
real live ``Views`` documents, chosen to cover every construct the audit found.
"""

from django.test import SimpleTestCase
from rest_framework.serializers import ValidationError

from data.table_spec import (
    html_signature,
    parse_view_content,
    spec_signature,
    validate_slug,
    validate_spec,
)

# --- fixtures ---------------------------------------------------------------

HEADER_ONLY = """
<h1>Word-final modifications - De-aspiration</h1>
<table><tr><th>Target Word</th><th>Dialect Form</th><th>Sound</th></tr></table>
"""

FLAT_TEMPLATE = """
<h1>Numerals - Forms - Multiplicatives</h1>
<table>
 <tr><th></th><th>Form</th><th>Origin</th></tr>
 [foreach]<tr><th data-rowspan="true">marker</th>
   <td>[{id: 2558, field: "form"}]</td><td>[{id: 2558, field: "source|language"}]</td></tr>[endforeach]
 [foreach]<tr><th data-rowspan="true">once</th>
   <td>[{id: 2559, field: "form"}]</td><td>[{id: 2559, field: "source|language"}]</td></tr>[endforeach]
</table>
"""

SECTIONS_AND_LIST = """
<h1>Adjective Inflection - Positive - Agreement</h1>
<table>
 <tr><th>Predicative</th><td>[{id: 49, field: answer}]</td></tr>
 <tr><th>Vocative</th><td>[{id: 50, field: answer}]</td></tr>
</table>
<h2>Case</h2>
<table>
 <tr><th>antepositional</th><td>[{id: 52, field: answer}]</td></tr>
 <tr><th>postpositional</th><td>[{id: 54, field: answer}]</td></tr>
</table>
"""

DEEP_TEMPLATE = """
<h1>Adpositions - Borrowed Prepositions</h1>
<table>
 <tr><th colspan="4">Meaning</th><th>Preposition</th><th>Preposition Origin</th></tr>
 [foreach]<tr>
   <th colspan="1" data-rowspan="start">Local</th>
   <th colspan="1" data-rowspan="start">Incorporative</th>
   <th colspan="2" data-rowspan="true">in, inside; into</th>
   <td>[{id: 267, field: "form"}]</td><td>[{id: 267, field: "source|language"}]</td>
 </tr>[endforeach]
 [foreach]<tr>
   <th colspan="1" data-rowspan="continue">Local</th>
   <th colspan="1" data-rowspan="continue">Incorporative</th>
   <th colspan="2" data-rowspan="true">out of, from inside of</th>
   <td>[{id: 268, field: "form"}]</td><td>[{id: 268, field: "source|language"}]</td>
 </tr>[endforeach]
</table>
"""

RAGGED_GRID = """
<h1>Article Inflection</h1>
<table>
 <caption>Definite</caption>
 <tr><th colspan="2"></th><th>Nominative</th><th>Oblique</th></tr>
 <tr><th rowspan="2">Singular</th><th>Masculine</th><td>[{id: 570, field: form}]</td><td>[{id: 571, field: form}]</td></tr>
 <tr><th>Feminine</th><td>[{id: 573, field: form}]</td><td>[{id: 574, field: form}]</td></tr>
 <tr><th>Plural</th><th></th><td>[{id: 576, field: form}]</td><td>[{id: 577, field: form}]</td></tr>
</table>
"""

NESTED_FIELDS = """
<h1>Indefinites - Etymology - Referent: Manner</h1>
<h2>Specific</h2>
<table>
 <tr><th>Form</th><th>Form source</th><th>Marker</th><th>Marker source</th></tr>
 [foreach]<tr>
   <td>[{id: 1431, field: form}]</td>
   <td>[{id: 1431, field: origin, tableField: source|language}]</td>
   <td>[foreach]<div>[{id: 1431, field: markers, tableField: marker}]</div>[endforeach]</td>
   <td>[foreach]<div>[{id: 1431, field: markers, tableField: origin.source|origin.language}]</div>[endforeach]</td>
 </tr>[endforeach]
</table>
"""

FIXTURES = {
    "header_only": HEADER_ONLY,
    "flat_template": FLAT_TEMPLATE,
    "sections_and_list": SECTIONS_AND_LIST,
    "deep_template": DEEP_TEMPLATE,
    "ragged_grid": RAGGED_GRID,
    "nested_fields": NESTED_FIELDS,
}


class ConverterParityTests(SimpleTestCase):
    def test_all_fixtures_convert_validate_and_match(self):
        for name, content in FIXTURES.items():
            with self.subTest(fixture=name):
                spec = parse_view_content(content)
                validate_spec(spec)  # raises on failure
                self.assertEqual(
                    html_signature(content),
                    spec_signature(spec),
                    f"structural signature mismatch for {name}",
                )

    def test_tablefield_folds_into_field_path(self):
        spec = parse_view_content(NESTED_FIELDS)
        cols = spec["sections"][0]["tables"][0]["columns"]
        fields = [c["cell"]["field"] for c in cols]
        self.assertEqual(
            fields,
            ["form", "origin.source|origin.language", "markers.marker",
             "markers.origin.source|markers.origin.language"],
        )
        self.assertEqual(cols[2]["cell"]["layout"], "stack")  # was [foreach]<div>
        self.assertEqual(cols[1]["cell"]["layout"], "inline")

    def test_ragged_grid_carries_native_rowspan_labels_down(self):
        spec = parse_view_content(RAGGED_GRID)
        t = spec["sections"][0]["tables"][0]
        self.assertEqual(t["kind"], "grid")
        self.assertEqual(t["caption"], "Definite")
        self.assertEqual([r["labels"] for r in t["rows"]],
                         [["Singular", "Masculine"], ["Singular", "Feminine"], ["Plural", ""]])
        self.assertEqual([c["questionId"] for c in t["rows"][0]["cells"]], [570, 571])

    def test_sections_split_on_h2(self):
        spec = parse_view_content(SECTIONS_AND_LIST)
        self.assertEqual([s["heading"] for s in spec["sections"]], [None, "Case"])
        self.assertEqual(spec["sections"][0]["tables"][0]["kind"], "list")

    def test_template_row_carries_question_id(self):
        spec = parse_view_content(FLAT_TEMPLATE)
        t = spec["sections"][0]["tables"][0]
        self.assertEqual(t["kind"], "template")
        self.assertEqual([r["questionId"] for r in t["rows"]], [2558, 2559])
        self.assertEqual([r["labels"] for r in t["rows"]], [["marker"], ["once"]])


class ValidatorTests(SimpleTestCase):
    def _valid(self):
        return parse_view_content(DEEP_TEMPLATE)

    def test_accepts_a_converted_spec(self):
        validate_spec(self._valid())

    def test_rejects_wrong_schema_version(self):
        spec = self._valid()
        spec["schemaVersion"] = 2
        with self.assertRaises(ValidationError):
            validate_spec(spec)

    def test_rejects_bad_field_path(self):
        spec = self._valid()
        spec["sections"][0]["tables"][0]["columns"][0]["cell"]["field"] = "../etc/passwd"
        with self.assertRaises(ValidationError):
            validate_spec(spec)

    def test_rejects_labels_longer_than_row_header_width(self):
        spec = self._valid()
        t = spec["sections"][0]["tables"][0]
        t["rowHeaderWidth"] = 1
        t["rows"][0]["labels"] = ["a", "b", "c"]
        with self.assertRaises(ValidationError):
            validate_spec(spec)

    def test_rejects_template_row_without_question_id(self):
        spec = self._valid()
        spec["sections"][0]["tables"][0]["rows"][0].pop("questionId")
        with self.assertRaises(ValidationError):
            validate_spec(spec)

    def test_slug_rules(self):
        validate_slug("adpositions-borrowed")
        for bad in ("Adpositions", "a--b", "-a", "a_b", "a b", ""):
            with self.assertRaises(ValidationError):
                validate_slug(bad)
