import json
from unittest.mock import MagicMock, patch

from django.contrib.auth.models import AnonymousUser
from django.test import RequestFactory, TestCase
from rest_framework.exceptions import ValidationError
from rest_framework.request import Request

from user.models import CustomUser


ALL_SAMPLES = [
    {"sample_ref": "AL-001", "dialect_name": "Dialect A", "visible": "Yes"},
    {"sample_ref": "AL-002", "dialect_name": "Dialect B", "visible": "Yes"},
    {"sample_ref": "HIDDEN-01", "dialect_name": "Dialect Hidden", "visible": "No"},
]


def _mock_arango_db():
    """Returns a mock db where:
       - collection.find({"visible": "Yes"}) yields only visible
       - collection.all() yields every sample
    """
    visible = [s for s in ALL_SAMPLES if s["visible"] == "Yes"]

    def find(query):
        return iter([s for s in ALL_SAMPLES if all(s.get(k) == v for k, v in query.items())])

    def all_():
        return iter(ALL_SAMPLES)

    collection = MagicMock()
    collection.find.side_effect = find
    collection.all.side_effect = all_

    def aql_execute(q, bind_vars=None):
        # Used for the phrase-search helpers: "FOR s IN Samples FILTER s.visible == 'Yes' RETURN s.sample_ref"
        # and the unfiltered variant.
        if "FILTER" in q:
            return iter([s["sample_ref"] for s in visible])
        return iter([s["sample_ref"] for s in ALL_SAMPLES])

    db = MagicMock()
    db.collection.return_value = collection
    db.aql.execute.side_effect = aql_execute
    return db


def _drf_request(user, method="get", path="/samples/", query=None, data=None):
    factory = RequestFactory()
    if method == "get":
        raw = factory.get(path, query or {})
    else:
        raw = factory.post(path, data or {}, content_type="application/json")
    req = Request(raw)
    req.user = user
    req.arangodb = _mock_arango_db()
    req.arango_error = None
    return req


class SampleViewSetQuerysetTests(TestCase):
    """Unit test SampleViewSet.get_queryset() visibility logic."""

    def setUp(self):
        self.admin = CustomUser.objects.create_user(
            username="admin", password="pw", is_global_admin=True
        )
        self.regular = CustomUser.objects.create_user(username="u", password="pw")

    def _make_viewset(self, user):
        from data.views import SampleViewSet
        vs = SampleViewSet()
        vs.request = _drf_request(user)
        return vs

    def test_anonymous_sees_only_visible(self):
        vs = self._make_viewset(AnonymousUser())
        refs = [s["sample_ref"] for s in vs.get_queryset()]
        self.assertIn("AL-001", refs)
        self.assertNotIn("HIDDEN-01", refs)

    def test_regular_user_sees_only_visible(self):
        vs = self._make_viewset(self.regular)
        refs = [s["sample_ref"] for s in vs.get_queryset()]
        self.assertNotIn("HIDDEN-01", refs)

    def test_admin_without_flag_sees_only_visible(self):
        self.admin.show_hidden_samples = False
        self.admin.save()
        vs = self._make_viewset(self.admin)
        refs = [s["sample_ref"] for s in vs.get_queryset()]
        self.assertNotIn("HIDDEN-01", refs)

    def test_admin_with_flag_sees_all(self):
        self.admin.show_hidden_samples = True
        self.admin.save()
        vs = self._make_viewset(self.admin)
        refs = [s["sample_ref"] for s in vs.get_queryset()]
        self.assertIn("AL-001", refs)
        self.assertIn("HIDDEN-01", refs)


class AnswerIncludeHiddenTests(TestCase):
    """Unit test AnswerViewSet.include_hidden()."""

    def setUp(self):
        self.admin = CustomUser.objects.create_user(
            username="admin", password="pw", is_global_admin=True
        )
        self.regular = CustomUser.objects.create_user(username="u", password="pw")

    def _make_viewset(self, user, method="get", query=None, data=None):
        from data.views import AnswerViewSet
        vs = AnswerViewSet()
        vs.request = _drf_request(user, method=method, path="/answers/", query=query, data=data)
        return vs

    def test_regular_user_default_false(self):
        vs = self._make_viewset(self.regular)
        self.assertFalse(vs.include_hidden())

    def test_query_param_true(self):
        vs = self._make_viewset(self.regular, query={"include_hidden": "true"})
        self.assertTrue(vs.include_hidden())

    def test_admin_with_flag_true(self):
        self.admin.show_hidden_samples = True
        self.admin.save()
        vs = self._make_viewset(self.admin)
        self.assertTrue(vs.include_hidden())

    def test_admin_without_flag_false(self):
        self.admin.show_hidden_samples = False
        self.admin.save()
        vs = self._make_viewset(self.admin)
        self.assertFalse(vs.include_hidden())


# ---------------------------------------------------------------------------
# Phrase test fixtures
# ---------------------------------------------------------------------------

PHRASES = [
    {"_key": "1", "phrase_ref": "80a", "phrase": "phrako",  "english": "brother", "sample": "AL-001", "has_recording": True},
    {"_key": "2", "phrase_ref": "80a", "phrase": "phral",   "english": "brother", "sample": "AL-002", "has_recording": False},
    {"_key": "3", "phrase_ref": "80a", "phrase": "phralo",  "english": "brother", "sample": "HIDDEN-01", "has_recording": False},
    {"_key": "4", "phrase_ref": "81",  "phrase": "phen",    "english": "sister",  "sample": "AL-001", "has_recording": True},
    {"_key": "5", "phrase_ref": "81",  "phrase": "pheni",   "english": "sister",  "sample": "AL-002", "has_recording": False},
]

VISIBLE_REFS = ["AL-001", "AL-002"]
ALL_REFS     = ["AL-001", "AL-002", "HIDDEN-01"]


def _phrase_db(phrases=None):
    """Mock ArangoDB for phrase search/export tests.
    Inspects the AQL string to route to the appropriate fixture data.
    """
    phrases = phrases or PHRASES

    def _label(sample):
        return f"Label {sample}"

    def aql_execute(query, bind_vars=None):
        bv = bind_vars or {}

        # ── phrase_list endpoint: COLLECT by phrase_ref ──────────────────
        if "COLLECT ref = p.phrase_ref" in query:
            seen, result = set(), []
            for p in phrases:
                if p["phrase_ref"] not in seen:
                    seen.add(p["phrase_ref"])
                    result.append({"phrase_ref": p["phrase_ref"], "english": p["english"]})
            return iter(result)

        # ── sample ref resolution (text search path) ─────────────────────
        if "RETURN s.sample_ref" in query:
            if "visible" in query.lower():
                return iter(VISIBLE_REFS)
            return iter(ALL_REFS)

        # ── phrase_ref exact-match path ───────────────────────────────────
        if "phrase.phrase_ref ==" in query:
            ref = bv.get("phrase_ref")
            result = [p for p in phrases if p["phrase_ref"] == ref]
            if bv.get("sample_refs"):
                result = [p for p in result if p["sample"] in bv["sample_refs"]]
            if "visible" in query.lower():
                result = [p for p in result if p["sample"] in VISIBLE_REFS]
            return iter([{**p, "sample_label": _label(p["sample"])} for p in result])

        # ── text search count query ───────────────────────────────────────
        if "COLLECT WITH COUNT INTO total" in query:
            q = bv.get("query", "")
            refs = bv.get("sample_refs", ALL_REFS)
            count = sum(
                1 for p in phrases
                if p["sample"] in refs and (q in p["phrase"].lower() or q in p["english"].lower())
            )
            return iter([count])

        # ── text search results query (PhraseSearch) ──────────────────────
        if "PhraseSearch" in query:
            q = bv.get("query", "")
            refs = bv.get("sample_refs", ALL_REFS)
            offset = bv.get("offset", 0)
            page_size = bv.get("page_size", 50)
            result = [
                p for p in phrases
                if p["sample"] in refs and (q in p["phrase"].lower() or q in p["english"].lower())
            ]
            return iter([{**p, "sample_label": _label(p["sample"])} for p in result[offset:offset + page_size]])

        return iter([])

    db = MagicMock()
    db.aql.execute.side_effect = aql_execute
    # collection().find() used by get_queryset
    col = MagicMock()
    col.find.side_effect = lambda q: iter([p for p in phrases if all(p.get(k) == v for k, v in q.items())])
    db.collection.return_value = col
    return db


def _phrase_request(user, method="get", data=None, query=None):
    factory = RequestFactory()
    path = "/phrases/"
    if method == "post":
        req = MagicMock(spec=Request)
        req.data = data or {}
        req.query_params = MagicMock(**{"get.return_value": None, "__contains__": lambda s, k: False})
    else:
        raw = factory.get(path, query or {})
        req = Request(raw)
    req.user = user
    req.arangodb = _phrase_db()
    req.arango_error = None
    return req


def _phrase_viewset(user, method="get", data=None, query=None):
    from data.views import PhraseViewSet
    vs = PhraseViewSet()
    vs.request = _phrase_request(user, method=method, data=data, query=query)
    vs.kwargs = {}
    vs.format_kwarg = None
    vs.action = "search" if method == "post" else "list"
    return vs


# ---------------------------------------------------------------------------
# GET /phrases/?sample= — get_queryset
# ---------------------------------------------------------------------------

class PhraseGetQuerysetTests(TestCase):

    def setUp(self):
        self.user = CustomUser.objects.create_user(username="u", password="pw")

    def test_returns_phrases_for_sample(self):
        vs = _phrase_viewset(self.user, query={"sample": "AL-001"})
        result = vs.get_queryset()
        refs = [p["sample"] for p in result]
        self.assertTrue(all(r == "AL-001" for r in refs))

    def test_missing_sample_raises_404(self):
        from rest_framework.exceptions import NotFound
        vs = _phrase_viewset(self.user)
        with self.assertRaises(NotFound):
            vs.get_queryset()

    def test_results_sorted_by_phrase_ref(self):
        vs = _phrase_viewset(self.user, query={"sample": "AL-001"})
        result = vs.get_queryset()
        phrase_refs = [p["phrase_ref"] for p in result]
        self.assertEqual(phrase_refs, sorted(phrase_refs, key=lambda x: (int(''.join(filter(str.isdigit, x)) or 0), x)))


# ---------------------------------------------------------------------------
# GET /phrases/list/ — phrase_list action
# ---------------------------------------------------------------------------

class PhraseListActionTests(TestCase):

    def setUp(self):
        self.user = CustomUser.objects.create_user(username="u", password="pw")

    def _call(self):
        from data.views import PhraseViewSet
        vs = PhraseViewSet()
        vs.request = _phrase_request(self.user)
        vs.kwargs = {}
        vs.format_kwarg = None
        return vs.phrase_list(vs.request)

    def test_returns_unique_phrase_refs(self):
        response = self._call()
        refs = [item["phrase_ref"] for item in response.data]
        self.assertEqual(len(refs), len(set(refs)))

    def test_returns_only_phrase_ref_and_english(self):
        response = self._call()
        for item in response.data:
            self.assertIn("phrase_ref", item)
            self.assertIn("english", item)

    def test_deduplicates_across_samples(self):
        # "80a" appears in 3 samples, "81" in 2 — should return only 2 unique entries
        response = self._call()
        self.assertEqual(len(response.data), 2)


# ---------------------------------------------------------------------------
# POST /phrases/search/ — text search path
# ---------------------------------------------------------------------------

class PhraseSearchTextTests(TestCase):

    def setUp(self):
        self.user = CustomUser.objects.create_user(username="u", password="pw")
        self.admin = CustomUser.objects.create_user(
            username="admin", password="pw", is_global_admin=True, show_hidden_samples=True
        )

    def _search(self, user, data):
        from data.views import PhraseViewSet
        vs = PhraseViewSet()
        vs.request = _phrase_request(user, method="post", data=data)
        vs.kwargs = {}
        vs.format_kwarg = None
        return vs.search(vs.request)

    def test_query_too_short_raises_validation_error(self):
        with self.assertRaises(ValidationError):
            self._search(self.user, {"query": "b"})

    def test_empty_query_raises_validation_error(self):
        with self.assertRaises(ValidationError):
            self._search(self.user, {})

    def test_text_search_returns_paginated_response(self):
        response = self._search(self.user, {"query": "brother"})
        self.assertIn("count", response.data)
        self.assertIn("results", response.data)
        self.assertIn("page", response.data)

    def test_text_search_finds_matching_phrases(self):
        response = self._search(self.user, {"query": "brother"})
        self.assertGreater(response.data["count"], 0)
        for phrase in response.data["results"]:
            self.assertIn("brother", phrase.get("english", "").lower())

    def test_regular_user_excludes_hidden_samples(self):
        response = self._search(self.user, {"query": "brother"})
        samples = [p["sample"] for p in response.data["results"]]
        self.assertNotIn("HIDDEN-01", samples)

    def test_admin_with_flag_includes_hidden_samples(self):
        response = self._search(self.admin, {"query": "brother"})
        samples = [p["sample"] for p in response.data["results"]]
        self.assertIn("HIDDEN-01", samples)

    def test_sample_refs_filter_restricts_results(self):
        response = self._search(self.user, {"query": "brother", "sample_refs": ["AL-001"]})
        samples = [p["sample"] for p in response.data["results"]]
        self.assertTrue(all(s == "AL-001" for s in samples))

    def test_results_include_sample_label(self):
        response = self._search(self.user, {"query": "sister"})
        for phrase in response.data["results"]:
            self.assertIn("sample_label", phrase)


# ---------------------------------------------------------------------------
# POST /phrases/search/ — phrase_ref path
# ---------------------------------------------------------------------------

class PhraseSearchByRefTests(TestCase):

    def setUp(self):
        self.user = CustomUser.objects.create_user(username="u", password="pw")
        self.admin = CustomUser.objects.create_user(
            username="admin", password="pw", is_global_admin=True, show_hidden_samples=True
        )

    def _search(self, user, data):
        from data.views import PhraseViewSet
        vs = PhraseViewSet()
        vs.request = _phrase_request(user, method="post", data=data)
        vs.kwargs = {}
        vs.format_kwarg = None
        return vs.search(vs.request)

    def test_phrase_ref_does_not_require_query(self):
        # Should not raise
        response = self._search(self.user, {"phrase_ref": "80a"})
        self.assertIn("results", response.data)

    def test_phrase_ref_with_short_query_does_not_raise(self):
        response = self._search(self.user, {"phrase_ref": "80a", "query": "b"})
        self.assertIn("results", response.data)

    def test_phrase_ref_returns_all_visible_samples(self):
        response = self._search(self.user, {"phrase_ref": "80a"})
        samples = [p["sample"] for p in response.data["results"]]
        self.assertIn("AL-001", samples)
        self.assertIn("AL-002", samples)

    def test_phrase_ref_excludes_hidden_for_regular_user(self):
        response = self._search(self.user, {"phrase_ref": "80a"})
        samples = [p["sample"] for p in response.data["results"]]
        self.assertNotIn("HIDDEN-01", samples)

    def test_phrase_ref_includes_hidden_for_admin(self):
        response = self._search(self.admin, {"phrase_ref": "80a"})
        samples = [p["sample"] for p in response.data["results"]]
        self.assertIn("HIDDEN-01", samples)

    def test_phrase_ref_returns_no_pagination(self):
        # count == len(results): everything returned in one shot
        response = self._search(self.user, {"phrase_ref": "80a"})
        self.assertEqual(response.data["count"], len(response.data["results"]))

    def test_phrase_ref_sample_refs_filter(self):
        response = self._search(self.user, {"phrase_ref": "80a", "sample_refs": ["AL-001"]})
        samples = [p["sample"] for p in response.data["results"]]
        self.assertEqual(samples, ["AL-001"])

    def test_unknown_phrase_ref_returns_empty(self):
        response = self._search(self.user, {"phrase_ref": "999z"})
        self.assertEqual(response.data["results"], [])
        self.assertEqual(response.data["count"], 0)

    def test_no_query_no_phrase_ref_raises(self):
        with self.assertRaises(ValidationError):
            self._search(self.user, {"phrase_ref": ""})


# ---------------------------------------------------------------------------
# POST /phrases/export/
# ---------------------------------------------------------------------------

class PhraseExportTests(TestCase):

    def setUp(self):
        self.user = CustomUser.objects.create_user(username="u", password="pw")
        self.admin = CustomUser.objects.create_user(
            username="admin", password="pw", is_global_admin=True, show_hidden_samples=True
        )

    def _export(self, user, data):
        from data.views import PhraseViewSet
        vs = PhraseViewSet()
        vs.request = _phrase_request(user, method="post", data=data)
        vs.kwargs = {}
        vs.format_kwarg = None
        return vs.export(vs.request)

    def test_text_export_requires_query(self):
        with self.assertRaises(ValidationError):
            self._export(self.user, {})

    def test_text_export_query_too_short(self):
        with self.assertRaises(ValidationError):
            self._export(self.user, {"query": "b"})

    def test_text_export_returns_list(self):
        response = self._export(self.user, {"query": "sister"})
        self.assertIsInstance(response.data, list)

    def test_text_export_excludes_hidden(self):
        response = self._export(self.user, {"query": "brother"})
        samples = [r["sample"] for r in response.data]
        self.assertNotIn("HIDDEN-01", samples)

    def test_text_export_admin_includes_hidden(self):
        response = self._export(self.admin, {"query": "brother"})
        samples = [r["sample"] for r in response.data]
        self.assertIn("HIDDEN-01", samples)

    def test_phrase_ref_export_no_query_needed(self):
        response = self._export(self.user, {"phrase_ref": "80a"})
        self.assertIsInstance(response.data, list)

    def test_phrase_ref_export_excludes_hidden(self):
        response = self._export(self.user, {"phrase_ref": "80a"})
        samples = [r["sample"] for r in response.data]
        self.assertNotIn("HIDDEN-01", samples)

    def test_phrase_ref_export_admin_includes_hidden(self):
        response = self._export(self.admin, {"phrase_ref": "80a"})
        samples = [r["sample"] for r in response.data]
        self.assertIn("HIDDEN-01", samples)

    def test_phrase_ref_export_contains_expected_fields(self):
        response = self._export(self.user, {"phrase_ref": "80a"})
        self.assertGreater(len(response.data), 0)
        for row in response.data:
            for field in ("phrase_ref", "sample", "phrase", "english", "has_recording"):
                self.assertIn(field, row)
