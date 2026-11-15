from unittest.mock import MagicMock

from django.http import QueryDict
from django.test import SimpleTestCase

from data.sample_scope import (
    SampleFilters,
    filter_sample_refs,
    parse_sample_filters,
    resolve_sample_scope,
)

SAMPLES = [
    {"sample_ref": "AL-001", "country_code": "AL", "dialect_group_name": "Balkan",
     "source_type": "RMS Questionaire", "migrant": "No",
     "contact_languages": [{"language": "Albanian", "level": "Current-L2"}]},
    {"sample_ref": "FIN-002", "country_code": "FIN", "dialect_group_name": "Northern",
     "source_type": "RMS Questionaire", "migrant": "No",
     "contact_languages": [{"language": "Swedish", "level": "Old-L2"}]},
    {"sample_ref": "PUB-001", "country_code": "YU", "dialect_group_name": "Balkan",
     "source_type": "Published Source", "migrant": "No", "contact_languages": None},
    {"sample_ref": "DE-010", "country_code": "DE", "dialect_group_name": None,
     "source_type": "External Fieldwork", "migrant": "Yes",
     "contact_languages": [{"language": "Turkish", "level": "Current-L2"}]},
    {"sample_ref": "XX-001", "country_code": "", "dialect_group_name": "",
     "source_type": "RMS Questionaire", "migrant": "", "contact_languages": []},
]


def _db():
    db = MagicMock()
    db.aql.execute.side_effect = lambda q, bind_vars=None: iter(SAMPLES)
    return db


def _refs(**data):
    return filter_sample_refs(_db(), parse_sample_filters(data))


class ParseSampleFiltersTests(SimpleTestCase):
    def test_empty_request_is_inactive(self):
        self.assertFalse(parse_sample_filters({}).is_active())
        self.assertIsNone(filter_sample_refs(MagicMock(), parse_sample_filters({})))

    def test_query_dict_accepts_repeated_and_comma_separated_params(self):
        qd = QueryDict("dialect_groups=Balkan&dialect_groups=Northern,Vlax&migrant=exclude")
        f = parse_sample_filters(qd)
        self.assertEqual(f.dialect_groups, {"Balkan", "Northern", "Vlax"})
        self.assertEqual(f.migrant, "exclude")

    def test_unknown_migrant_mode_falls_back_to_all(self):
        self.assertEqual(parse_sample_filters({"migrant": "maybe"}).migrant, "all")

    def test_country_codes_expand_to_legacy_aliases(self):
        # Regression: canonical "FI" (what the client sends) must still match
        # un-normalized legacy "FIN" values (see normalize_country_codes).
        self.assertIn("FIN", parse_sample_filters({"country_codes": ["FI"]}).country_codes)


class FilterSampleRefsTests(SimpleTestCase):
    def test_country(self):
        self.assertEqual(_refs(country_codes=["AL", "FI"]), ["AL-001", "FIN-002"])

    def test_country_none_matches_missing_country(self):
        self.assertEqual(_refs(country_codes=["__none__"]), ["XX-001"])

    def test_dialect_group_with_none(self):
        self.assertEqual(_refs(dialect_groups=["Balkan"]), ["AL-001", "PUB-001"])
        self.assertEqual(_refs(dialect_groups=["__none__"]), ["DE-010", "XX-001"])

    def test_source_type(self):
        self.assertEqual(_refs(source_type="Published Source"), ["PUB-001"])
        self.assertEqual(_refs(source_type="External Fieldwork"), ["DE-010"])

    def test_migrant_modes(self):
        self.assertEqual(_refs(migrant="only"), None)  # "only" was dropped: no narrowing
        self.assertEqual(_refs(migrant="exclude"), ["AL-001", "FIN-002", "PUB-001", "XX-001"])

    def test_contact_language_any_level_and_specific_level(self):
        self.assertEqual(_refs(contact_languages=["any:swedish"]), ["FIN-002"])
        self.assertEqual(_refs(contact_languages=["Current-L2:Swedish"]), [])
        self.assertEqual(_refs(contact_languages=["Current-L2:Turkish", "any:Albanian"]), ["AL-001", "DE-010"])

    def test_filters_combine_with_and(self):
        self.assertEqual(_refs(dialect_groups=["Balkan"], source_type="RMS Questionaire"), ["AL-001"])

    def test_active_filter_matching_nothing_is_empty_not_none(self):
        self.assertEqual(_refs(dialect_groups=["Vlax"]), [])

    def test_narrows_explicit_refs(self):
        f = parse_sample_filters({"migrant": "exclude"})
        self.assertEqual(filter_sample_refs(_db(), f, ["DE-010", "AL-001"]), ["AL-001"])


class ResolveSampleScopeTests(SimpleTestCase):
    VISIBLE = ["AL-001", "FIN-002", "PUB-001", "DE-010"]

    def test_no_filters_returns_visible_set(self):
        self.assertEqual(sorted(resolve_sample_scope(MagicMock(), {}, [], self.VISIBLE)), sorted(self.VISIBLE))

    def test_filters_narrow_visible_set(self):
        out = resolve_sample_scope(_db(), {"dialect_groups": ["Balkan"]}, [], self.VISIBLE)
        self.assertEqual(sorted(out), ["AL-001", "PUB-001"])

    def test_explicit_ref_excluded_when_not_visible(self):
        # Regression: an explicit sample_refs entry the caller isn't
        # authorized to see (a hidden sample handed back by a stale client
        # cache after logout, 2026-09-16) must never be served just because
        # the client named it. Explicit refs narrow the visible set; they
        # never extend it.
        out = resolve_sample_scope(MagicMock(), {}, ["AL-001", "HIDDEN-01"], self.VISIBLE)
        self.assertEqual(out, ["AL-001"])

    def test_explicit_refs_and_filters_intersect(self):
        out = resolve_sample_scope(_db(), {"migrant": "exclude"}, ["AL-001", "DE-010"], self.VISIBLE)
        self.assertEqual(out, ["AL-001"])


class SampleFiltersMatchTests(SimpleTestCase):
    def test_no_filters_match_everything(self):
        self.assertTrue(all(SampleFilters().matches(s) for s in SAMPLES))
