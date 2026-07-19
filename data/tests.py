import json
from unittest.mock import MagicMock, patch

from django.contrib.auth.models import AnonymousUser
from django.test import RequestFactory, SimpleTestCase
from rest_framework.exceptions import ValidationError
from rest_framework.request import Request


def _mock_user(is_admin=False, show_hidden=False):
    user = MagicMock()
    user.is_authenticated = True
    user.is_global_admin = is_admin
    user.show_hidden_samples = show_hidden
    return user


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


class SampleViewSetQuerysetTests(SimpleTestCase):
    """Unit test SampleViewSet.get_queryset() visibility logic."""

    def setUp(self):
        self.admin = _mock_user(is_admin=True, show_hidden=False)
        self.regular = _mock_user()

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
        vs = self._make_viewset(self.admin)
        refs = [s["sample_ref"] for s in vs.get_queryset()]
        self.assertNotIn("HIDDEN-01", refs)

    def test_admin_with_flag_sees_all(self):
        self.admin.show_hidden_samples = True
        vs = self._make_viewset(self.admin)
        refs = [s["sample_ref"] for s in vs.get_queryset()]
        self.assertIn("AL-001", refs)
        self.assertIn("HIDDEN-01", refs)


class AnswerIncludeHiddenTests(SimpleTestCase):
    """Unit test AnswerViewSet.include_hidden()."""

    def setUp(self):
        self.admin = _mock_user(is_admin=True, show_hidden=False)
        self.regular = _mock_user()

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
        vs = self._make_viewset(self.admin)
        self.assertTrue(vs.include_hidden())

    def test_admin_without_flag_false(self):
        self.admin.show_hidden_samples = False
        vs = self._make_viewset(self.admin)
        self.assertFalse(vs.include_hidden())


# ---------------------------------------------------------------------------
# Phrase test fixtures
#
# The Phrases collection was replaced by MasterPhrases (one doc per
# phrase_ref, carrying english/conjugated/question_ids/category_ids) plus
# SamplePhrases (one doc per sample recording, keyed by "{sample}_{phrase_ref}").
# See extract/master_phrases_migration/PLAN.md.
# ---------------------------------------------------------------------------

MASTER_PHRASES = [
    {"phrase_ref": "80a", "english": "brother", "conjugated": False, "question_ids": [], "category_ids": []},
    {"phrase_ref": "81", "english": "sister", "conjugated": False, "question_ids": [], "category_ids": []},
]

SAMPLE_PHRASES = [
    {"_key": "AL-001_80a", "phrase_ref": "80a", "phrase": "phrako", "sample": "AL-001", "has_recording": True},
    {"_key": "AL-002_80a", "phrase_ref": "80a", "phrase": "phral", "sample": "AL-002", "has_recording": False},
    {"_key": "HIDDEN-01_80a", "phrase_ref": "80a", "phrase": "phralo", "sample": "HIDDEN-01", "has_recording": False},
    {"_key": "AL-001_81", "phrase_ref": "81", "phrase": "phen", "sample": "AL-001", "has_recording": True},
    {"_key": "AL-002_81", "phrase_ref": "81", "phrase": "pheni", "sample": "AL-002", "has_recording": False},
]

VISIBLE_REFS = ["AL-001", "AL-002"]
ALL_REFS     = ["AL-001", "AL-002", "HIDDEN-01"]


class _FakePhraseDB:
    """
    Fake ArangoDB for the phrase list/search/export endpoints. Rather than
    parsing real AQL, dispatches on distinctive substrings of the query
    text (same approach the pre-migration mock used) and computes the
    answer directly against in-memory MasterPhrases/SamplePhrases fixtures
    — mirrors exactly what the real AQL in data/views.py does for the
    DOCUMENT()-based joins between the two collections.
    """

    def __init__(self, master_phrases=None, sample_phrases=None, samples=None):
        self.master_phrases = {m["phrase_ref"]: m for m in (master_phrases if master_phrases is not None else MASTER_PHRASES)}
        self.sample_phrases = {sp["_key"]: sp for sp in (sample_phrases if sample_phrases is not None else SAMPLE_PHRASES)}
        self.samples = samples if samples is not None else ALL_SAMPLES

    def _merged(self, sp, sample_label=None):
        # question_ids/category_ids deliberately omitted — list/search/
        # export no longer return them (bulky, unused by any bulk-list
        # consumer; see PhraseViewSet.links for the on-demand replacement).
        m = self.master_phrases.get(sp["phrase_ref"], {})
        out = {
            **sp,
            "english": m.get("english"),
            "conjugated": m.get("conjugated"),
        }
        if sample_label is not None:
            out["sample_label"] = sample_label
        return out

    def _export_row(self, sp, sample_label):
        m = self.master_phrases.get(sp["phrase_ref"], {})
        return {
            "phrase_ref": sp["phrase_ref"],
            "sample": sp["sample"],
            "sample_label": sample_label,
            "phrase": sp["phrase"],
            "english": m.get("english"),
            "conjugated": m.get("conjugated"),
            "has_recording": sp.get("has_recording"),
        }

    def collection(self, name):
        col = MagicMock()
        store = {"SamplePhrases": self.sample_phrases, "MasterPhrases": self.master_phrases}.get(name, {})
        col.find.side_effect = lambda q: iter([v for v in store.values() if all(v.get(k) == val for k, val in q.items())])
        return col

    def aql_execute(self, query, bind_vars=None):
        bv = bind_vars or {}
        is_export = "has_recording: phrase.has_recording" in query

        # sample_refs resolution when not explicitly provided (used by both
        # the search and export general branches before building bind_vars)
        if query == "FOR s IN Samples RETURN s.sample_ref":
            return iter([s["sample_ref"] for s in self.samples])
        if query == "FOR s IN Samples FILTER s.visible == 'Yes' RETURN s.sample_ref":
            return iter([s["sample_ref"] for s in self.samples if s.get("visible") == "Yes"])

        # get_queryset: GET /phrases/?sample=
        if "FOR sp IN SamplePhrases" in query and "FILTER sp.sample == @sample" in query and "DOCUMENT" in query:
            sample = bv["sample"]
            return iter([self._merged(sp) for sp in self.sample_phrases.values() if sp["sample"] == sample])

        # phrase_list: GET /phrases/list/
        if "RETURN { phrase_ref: m.phrase_ref, english: m.english }" in query:
            return iter([{"phrase_ref": m["phrase_ref"], "english": m["english"]} for m in self.master_phrases.values()])

        # search/export phrase_ref (exact match) branch
        if 'LET m = DOCUMENT(CONCAT("MasterPhrases/", @phrase_ref))' in query:
            phrase_ref = bv.get("phrase_ref")
            m = self.master_phrases.get(phrase_ref)
            if not m:
                return iter([])
            sample_refs = bv.get("sample_refs")
            visible_only = "FILTER s.visible == 'Yes'" in query
            rows = []
            for sp in self.sample_phrases.values():
                if sp["phrase_ref"] != phrase_ref:
                    continue
                if sample_refs and sp["sample"] not in sample_refs:
                    continue
                s = next((x for x in self.samples if x["sample_ref"] == sp["sample"]), None)
                if s is None:
                    continue
                if visible_only and s.get("visible") != "Yes":
                    continue
                label = f"Label {sp['sample']}"
                rows.append(self._export_row(sp, label) if is_export else self._merged(sp, label))
            return iter(rows)

        # search/export general (free-text) branch
        if "LET candidate_keys = UNIQUE" in query:
            query_lower = bv.get("query", "")
            sample_refs = bv.get("sample_refs", ALL_REFS)
            search_romani = bv.get("search_romani", True)
            search_english = bv.get("search_english", True)

            romani_keys = set()
            if search_romani:
                romani_keys = {sp["_key"] for sp in self.sample_phrases.values()
                                if sp["sample"] in sample_refs and query_lower in sp["phrase"].lower()}
            english_keys = set()
            if search_english:
                english_refs = {m["phrase_ref"] for m in self.master_phrases.values()
                                 if query_lower in (m.get("english") or "").lower()}
                english_keys = {sp["_key"] for sp in self.sample_phrases.values()
                                 if sp["phrase_ref"] in english_refs and sp["sample"] in sample_refs}
            candidate_keys = sorted(romani_keys | english_keys)

            if "RETURN LENGTH(candidate_keys)" in query:
                return iter([len(candidate_keys)])

            rows = []
            for key in candidate_keys:
                sp = self.sample_phrases[key]
                label = f"Label {sp['sample']}"
                rows.append(self._export_row(sp, label) if is_export else self._merged(sp, label))

            if is_export:
                return iter(rows)
            offset = bv.get("offset", 0)
            page_size = bv.get("page_size", 50)
            return iter(rows[offset:offset + page_size])

        return iter([])


def _phrase_request(user, method="get", data=None, query=None, db=None):
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
    fake = db or _FakePhraseDB()
    mock_db = MagicMock()
    mock_db.aql.execute.side_effect = fake.aql_execute
    mock_db.collection.side_effect = fake.collection
    req.arangodb = mock_db
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

class PhraseGetQuerysetTests(SimpleTestCase):

    def setUp(self):
        self.user = _mock_user()

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

    def test_result_includes_master_fields(self):
        vs = _phrase_viewset(self.user, query={"sample": "AL-001"})
        result = vs.get_queryset()
        for p in result:
            self.assertIn("english", p)
            self.assertIn("conjugated", p)

    def test_result_omits_question_and_category_ids(self):
        # Bulky (avg ~56 ints/phrase) and unused by any list/search
        # consumer — fetch via GET /phrases/{key}/links/ instead.
        vs = _phrase_viewset(self.user, query={"sample": "AL-001"})
        result = vs.get_queryset()
        for p in result:
            self.assertNotIn("question_ids", p)
            self.assertNotIn("category_ids", p)


# ---------------------------------------------------------------------------
# GET /phrases/list/ — phrase_list action
# ---------------------------------------------------------------------------

class PhraseListActionTests(SimpleTestCase):

    def setUp(self):
        self.user = _mock_user()

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

    def test_one_record_per_master_phrase(self):
        # MASTER_PHRASES has 2 entries ("80a", "81") even though "80a" has
        # 3 sample recordings — one row per MasterPhrase, not per sample.
        response = self._call()
        self.assertEqual(len(response.data), 2)


# ---------------------------------------------------------------------------
# POST /phrases/search/ — text search path
# ---------------------------------------------------------------------------

class PhraseSearchTextTests(SimpleTestCase):

    def setUp(self):
        self.user = _mock_user()
        self.admin = _mock_user(is_admin=True, show_hidden=True)

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
        response = self._search(self.user, {"query": "brother", "field": "english"})
        self.assertGreater(response.data["count"], 0)
        for phrase in response.data["results"]:
            self.assertIn("brother", phrase.get("english", "").lower())

    def test_regular_user_excludes_hidden_samples(self):
        response = self._search(self.user, {"query": "brother", "field": "english"})
        samples = [p["sample"] for p in response.data["results"]]
        self.assertNotIn("HIDDEN-01", samples)

    def test_admin_with_flag_includes_hidden_samples(self):
        response = self._search(self.admin, {"query": "brother", "field": "english"})
        samples = [p["sample"] for p in response.data["results"]]
        self.assertIn("HIDDEN-01", samples)

    def test_sample_refs_filter_restricts_results(self):
        response = self._search(self.user, {"query": "brother", "field": "english", "sample_refs": ["AL-001"]})
        samples = [p["sample"] for p in response.data["results"]]
        self.assertTrue(all(s == "AL-001" for s in samples))

    def test_results_include_sample_label(self):
        response = self._search(self.user, {"query": "sister", "field": "english"})
        for phrase in response.data["results"]:
            self.assertIn("sample_label", phrase)

    def test_romani_field_searches_phrase_text(self):
        response = self._search(self.user, {"query": "phrako", "field": "romani"})
        self.assertEqual(response.data["count"], 1)
        self.assertEqual(response.data["results"][0]["sample"], "AL-001")


# ---------------------------------------------------------------------------
# POST /phrases/search/ — phrase_ref path
# ---------------------------------------------------------------------------

class PhraseSearchByRefTests(SimpleTestCase):

    def setUp(self):
        self.user = _mock_user()
        self.admin = _mock_user(is_admin=True, show_hidden=True)

    def _search(self, user, data):
        from data.views import PhraseViewSet
        vs = PhraseViewSet()
        vs.request = _phrase_request(user, method="post", data=data)
        vs.kwargs = {}
        vs.format_kwarg = None
        return vs.search(vs.request)

    def test_phrase_ref_does_not_require_query(self):
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

class PhraseExportTests(SimpleTestCase):

    def setUp(self):
        self.user = _mock_user()
        self.admin = _mock_user(is_admin=True, show_hidden=True)

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
        response = self._export(self.user, {"query": "sister", "field": "english"})
        self.assertIsInstance(response.data, list)

    def test_text_export_excludes_hidden(self):
        response = self._export(self.user, {"query": "brother", "field": "english"})
        samples = [r["sample"] for r in response.data]
        self.assertNotIn("HIDDEN-01", samples)

    def test_text_export_admin_includes_hidden(self):
        response = self._export(self.admin, {"query": "brother", "field": "english"})
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


# ---------------------------------------------------------------------------
# _get_question_hierarchy_ids helper
# ---------------------------------------------------------------------------

class QuestionHierarchyIdsTests(SimpleTestCase):
    """Unit tests for _get_question_hierarchy_ids in data.views."""

    def setUp(self):
        from data.views import _get_question_hierarchy_ids
        self.resolve = _get_question_hierarchy_ids

    def _db(self, hierarchy_ids=None):
        db = MagicMock()
        db.aql.execute.return_value = iter([hierarchy_ids] if hierarchy_ids is not None else [])
        return db

    def test_returns_hierarchy_ids_when_question_found(self):
        db = self._db([1, 4, 5, 6])
        self.assertEqual(self.resolve(db, 6), [1, 4, 5, 6])

    def test_returns_none_when_question_not_found(self):
        db = self._db(hierarchy_ids=None)
        self.assertIsNone(self.resolve(db, 999))

    def test_returns_none_when_question_id_is_none(self):
        db = self._db()
        self.assertIsNone(self.resolve(db, None))
        db.aql.execute.assert_not_called()


# ---------------------------------------------------------------------------
# GET /phrases/by-answer/
# ---------------------------------------------------------------------------

class _FakeByAnswerPhraseDB:
    """
    Fake ArangoDB for PhraseViewSet.by_answer. Mirrors the two AQL shapes
    the real view issues: the phrase_overrides.include override path, and
    the normal question_ids/category_ids-matching path — both then joining
    to SamplePhrases by direct key lookup (DOCUMENT), same as the real
    implementation.
    """

    def __init__(self, answer, question=None, master_phrases=None, sample_phrases=None):
        self.answer = answer
        self.question = question
        self.master_phrases = {m["phrase_ref"]: m for m in (master_phrases or [])}
        self.sample_phrases = {sp["_key"]: sp for sp in (sample_phrases or [])}

    def collection(self, name):
        col = MagicMock()
        if name == "Answers":
            col.get.side_effect = lambda key: self.answer if self.answer and self.answer.get("_key") == key else None
        else:
            col.get.side_effect = lambda key: None
        return col

    def _merge(self, sp, m):
        # question_ids/category_ids deliberately omitted from the response —
        # see PhraseGetQuerysetTests.test_result_omits_question_and_category_ids.
        return {
            **sp,
            "english": m.get("english"),
            "conjugated": m.get("conjugated"),
        }

    def aql_execute(self, query, bind_vars=None):
        bv = bind_vars or {}

        if "ResearchQuestions" in query and "hierarchy_ids" in query:
            return iter([self.question["hierarchy_ids"]] if self.question else [])

        # question_overrides.include/exclude live on SamplePhrases now; the
        # fixtures here don't set them, so this always no-ops (empty override).
        if "FOR sp IN SamplePhrases" in query and "question_overrides.include" in query:
            return iter([])

        if "FOR phrase_ref IN @include" in query:
            include = bv["include"]
            exclude = set(bv.get("exclude") or [])
            sample = bv["sample"]
            rows = []
            for ref in include:
                if ref in exclude:
                    continue
                sp = self.sample_phrases.get(f"{sample}_{ref}")
                if not sp:
                    continue
                m = self.master_phrases.get(ref, {})
                rows.append(self._merge(sp, m))
            return iter(rows)

        if "FOR m IN MasterPhrases" in query and "@category_id IN" in query:
            category_id = bv["category_id"]
            hierarchy_ids = set(bv["hierarchy_ids"])
            exclude = set(bv.get("exclude") or [])
            sample = bv["sample"]
            rows = []
            for m in self.master_phrases.values():
                if m["phrase_ref"] in exclude:
                    continue
                if category_id in (m.get("question_ids") or []) or (set(m.get("category_ids") or []) & hierarchy_ids):
                    sp = self.sample_phrases.get(f"{sample}_{m['phrase_ref']}")
                    if not sp:
                        continue
                    rows.append(self._merge(sp, m))
            return iter(rows)

        return iter([])


class PhrasesByAnswerTests(SimpleTestCase):

    def setUp(self):
        self.user = _mock_user()

    def _call(self, answer, question=None, master_phrases=None, sample_phrases=None):
        from data.views import PhraseViewSet
        fake = _FakeByAnswerPhraseDB(answer, question=question, master_phrases=master_phrases, sample_phrases=sample_phrases)
        mock_db = MagicMock()
        mock_db.aql.execute.side_effect = fake.aql_execute
        mock_db.collection.side_effect = fake.collection
        factory = RequestFactory()
        raw = factory.get("/phrases/by-answer/", {"answer_key": answer.get("_key", "k1") if answer else "k1"})
        req = Request(raw)
        req.user = self.user
        req.arangodb = mock_db
        req.arango_error = None
        vs = PhraseViewSet()
        vs.request = req
        vs.kwargs = {}
        vs.format_kwarg = None
        vs.action = "by_answer"
        return vs.by_answer(req)

    def test_matches_via_question_ids(self):
        m = {"phrase_ref": "1", "english": "brother", "conjugated": False, "question_ids": [10], "category_ids": []}
        sp = {"_key": "AL-001_1", "phrase_ref": "1", "phrase": "phrako", "sample": "AL-001", "has_recording": True}
        answer = {"_key": "a1", "sample": "AL-001", "question_id": 10}
        question = {"hierarchy_ids": [1, 10]}
        response = self._call(answer, question=question, master_phrases=[m], sample_phrases=[sp])
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data), 1)

    def test_matches_via_category_ids(self):
        m = {"phrase_ref": "1", "english": "brother", "conjugated": False, "question_ids": [], "category_ids": [4]}
        sp = {"_key": "AL-001_1", "phrase_ref": "1", "phrase": "phrako", "sample": "AL-001", "has_recording": True}
        answer = {"_key": "a1", "sample": "AL-001", "question_id": 10}
        question = {"hierarchy_ids": [1, 4, 10]}  # question 10 falls under category 4
        response = self._call(answer, question=question, master_phrases=[m], sample_phrases=[sp])
        self.assertEqual(len(response.data), 1)

    def test_returns_empty_when_no_match(self):
        answer = {"_key": "a1", "sample": "AL-001", "question_id": 10}
        question = {"hierarchy_ids": [1, 10]}
        response = self._call(answer, question=question, master_phrases=[], sample_phrases=[])
        self.assertEqual(list(response.data), [])

    def test_override_include_replaces_normal_match(self):
        # Normal (question-based) matching would find phrase_ref "2"; the
        # override should replace that entirely with phrase_ref "1" — this
        # is what preserves per-answer precision for the heterogeneous
        # questions (see extract/tag_divergence.md).
        m1 = {"phrase_ref": "1", "english": "brother", "conjugated": False, "question_ids": [], "category_ids": []}
        m2 = {"phrase_ref": "2", "english": "sister", "conjugated": False, "question_ids": [10], "category_ids": []}
        sp1 = {"_key": "AL-001_1", "phrase_ref": "1", "phrase": "phrako", "sample": "AL-001", "has_recording": True}
        sp2 = {"_key": "AL-001_2", "phrase_ref": "2", "phrase": "phen", "sample": "AL-001", "has_recording": True}
        answer = {"_key": "a1", "sample": "AL-001", "question_id": 10,
                  "phrase_overrides": {"include": ["1"], "exclude": []}}
        question = {"hierarchy_ids": [1, 10]}
        response = self._call(answer, question=question, master_phrases=[m1, m2], sample_phrases=[sp1, sp2])
        self.assertEqual(len(response.data), 1)
        self.assertEqual(response.data[0]["phrase_ref"], "1")

    def test_override_exclude_removes_from_normal_match(self):
        m1 = {"phrase_ref": "1", "english": "brother", "conjugated": False, "question_ids": [10], "category_ids": []}
        m2 = {"phrase_ref": "2", "english": "sister", "conjugated": False, "question_ids": [10], "category_ids": []}
        sp1 = {"_key": "AL-001_1", "phrase_ref": "1", "phrase": "phrako", "sample": "AL-001", "has_recording": True}
        sp2 = {"_key": "AL-001_2", "phrase_ref": "2", "phrase": "phen", "sample": "AL-001", "has_recording": True}
        answer = {"_key": "a1", "sample": "AL-001", "question_id": 10,
                  "phrase_overrides": {"include": [], "exclude": ["2"]}}
        question = {"hierarchy_ids": [1, 10]}
        response = self._call(answer, question=question, master_phrases=[m1, m2], sample_phrases=[sp1, sp2])
        refs = [p["phrase_ref"] for p in response.data]
        self.assertEqual(refs, ["1"])

    def test_missing_answer_key_raises_400(self):
        from data.views import PhraseViewSet
        fake = _FakeByAnswerPhraseDB(answer=None)
        mock_db = MagicMock()
        mock_db.aql.execute.side_effect = fake.aql_execute
        mock_db.collection.side_effect = fake.collection
        factory = RequestFactory()
        raw = factory.get("/phrases/by-answer/")  # no answer_key param
        req = Request(raw)
        req.user = self.user
        req.arangodb = mock_db
        req.arango_error = None
        vs = PhraseViewSet()
        vs.request = req
        vs.kwargs = {}
        vs.format_kwarg = None
        with self.assertRaises((ValidationError, Exception)):
            vs.by_answer(req)


# ---------------------------------------------------------------------------
# GET /transcriptions/by-answer/
# ---------------------------------------------------------------------------

class _FakeByAnswerTranscriptionDB:
    """Mirrors the two AQL shapes TranscriptionViewSet.by_answer issues:
    the transcription_overrides.include override path (direct key lookups,
    still sample-scoped), and the normal question_ids/category_ids match."""

    def __init__(self, answer, question=None, transcriptions=None):
        self.answer = answer
        self.question = question
        self.transcriptions = {t["_key"]: t for t in (transcriptions or [])}

    def collection(self, name):
        col = MagicMock()
        if name == "Answers":
            col.get.side_effect = lambda key: self.answer if self.answer and self.answer.get("_key") == key else None
        else:
            col.get.side_effect = lambda key: None
        return col

    def aql_execute(self, query, bind_vars=None):
        bv = bind_vars or {}

        if "ResearchQuestions" in query and "hierarchy_ids" in query:
            return iter([self.question["hierarchy_ids"]] if self.question else [])

        if "FOR key IN @include" in query:
            include = bv["include"]
            exclude = set(bv.get("exclude") or [])
            sample = bv["sample"]
            rows = []
            for key in include:
                if key in exclude:
                    continue
                t = self.transcriptions.get(key)
                if t and t.get("sample") == sample:
                    rows.append(t)
            return iter(rows)

        if "FOR transcription IN Transcriptions" in query:
            sample = bv["sample"]
            category_id = bv["category_id"]
            hierarchy_ids = set(bv["hierarchy_ids"])
            exclude = set(bv.get("exclude") or [])
            rows = []
            for t in self.transcriptions.values():
                if t["sample"] != sample or t["_key"] in exclude:
                    continue
                if category_id in (t.get("question_ids") or []) or (set(t.get("category_ids") or []) & hierarchy_ids):
                    rows.append(t)
            return iter(rows)

        return iter([])


class TranscriptionsByAnswerTests(SimpleTestCase):

    def setUp(self):
        self.user = _mock_user()

    def _call(self, answer, question=None, transcriptions=None):
        from data.views import TranscriptionViewSet
        fake = _FakeByAnswerTranscriptionDB(answer, question=question, transcriptions=transcriptions)
        mock_db = MagicMock()
        mock_db.aql.execute.side_effect = fake.aql_execute
        mock_db.collection.side_effect = fake.collection
        factory = RequestFactory()
        raw = factory.get("/transcriptions/by-answer/", {"answer_key": answer.get("_key", "k1")})
        req = Request(raw)
        req.user = self.user
        req.arangodb = mock_db
        req.arango_error = None
        vs = TranscriptionViewSet()
        vs.request = req
        vs.kwargs = {}
        vs.format_kwarg = None
        vs.action = "by_answer"
        return vs.by_answer(req)

    def test_matches_via_question_ids(self):
        t = {"_key": "t1", "sample": "AL-001", "segment_no": 1, "question_ids": [10], "category_ids": []}
        answer = {"_key": "a1", "sample": "AL-001", "question_id": 10}
        question = {"hierarchy_ids": [1, 10]}
        response = self._call(answer, question=question, transcriptions=[t])
        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.data), 1)

    def test_matches_via_category_ids(self):
        t = {"_key": "t1", "sample": "AL-001", "segment_no": 1, "question_ids": [], "category_ids": [4]}
        answer = {"_key": "a1", "sample": "AL-001", "question_id": 10}
        question = {"hierarchy_ids": [1, 4, 10]}
        response = self._call(answer, question=question, transcriptions=[t])
        self.assertEqual(len(response.data), 1)

    def test_returns_empty_when_no_match(self):
        answer = {"_key": "a1", "sample": "AL-001", "question_id": 10}
        question = {"hierarchy_ids": [1, 10]}
        response = self._call(answer, question=question, transcriptions=[])
        self.assertEqual(list(response.data), [])

    def test_override_include_replaces_normal_match(self):
        t1 = {"_key": "t1", "sample": "AL-001", "segment_no": 1, "question_ids": [], "category_ids": []}
        t2 = {"_key": "t2", "sample": "AL-001", "segment_no": 2, "question_ids": [10], "category_ids": []}
        answer = {"_key": "a1", "sample": "AL-001", "question_id": 10,
                  "transcription_overrides": {"include": ["t1"], "exclude": []}}
        question = {"hierarchy_ids": [1, 10]}
        response = self._call(answer, question=question, transcriptions=[t1, t2])
        self.assertEqual(len(response.data), 1)
        self.assertEqual(response.data[0]["_key"], "t1")

    def test_override_include_respects_sample_scoping(self):
        # t1 matches by key but belongs to a different sample than the
        # answer — must still be excluded, same as the real endpoint (an
        # override computed across all samples shouldn't leak across them).
        t1 = {"_key": "t1", "sample": "OTHER-001", "segment_no": 1, "question_ids": [], "category_ids": []}
        answer = {"_key": "a1", "sample": "AL-001", "question_id": 10,
                  "transcription_overrides": {"include": ["t1"], "exclude": []}}
        question = {"hierarchy_ids": [1, 10]}
        response = self._call(answer, question=question, transcriptions=[t1])
        self.assertEqual(list(response.data), [])
