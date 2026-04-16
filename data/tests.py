from unittest.mock import MagicMock

from django.contrib.auth.models import AnonymousUser
from django.test import RequestFactory, TestCase
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
