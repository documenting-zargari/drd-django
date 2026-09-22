"""HTTP-level tests for ViewViewSet's PATCH endpoint (the write path added on
top of the existing read-only Views API). No prior test coverage of
ViewViewSet existed before this file.
"""

from unittest.mock import MagicMock

from django.test import SimpleTestCase
from rest_framework.exceptions import NotFound
from rest_framework.permissions import AllowAny
from rest_framework.request import Request

from data.views import ViewViewSet
from user.permissions import IsGlobalAdmin

VALID_SPEC = {
    "schemaVersion": 1,
    "title": "Numerals - Forms",
    "sections": [{
        "heading": None,
        "tables": [{
            "kind": "list",
            "caption": None,
            "rowHeaderWidth": 1,
            "columnHeader": [[{"label": "Marker"}, {"label": "Meaning"}]],
            "columns": [{"cell": {"field": "answer"}}],
            "multiAnswer": "combine",
            "rows": [{"labels": ["once"], "cells": [{"field": "answer"}]}],
        }],
    }],
}


class _FakeViewDB:
    """In-memory Views collection keyed by _key, with slug/filename lookup
    via AQL like the real _resolve_via_db chain."""

    def __init__(self, docs):
        self.docs = {d["_key"]: dict(d) for d in docs}

    def collection(self, name):
        col = MagicMock()
        col.get.side_effect = lambda key: self.docs.get(key)

        def _update(doc):
            key = doc["_key"]
            self.docs[key].update({k: v for k, v in doc.items() if k != "_key"})
            return {"_key": key}

        col.update.side_effect = _update
        return col

    def aql_execute(self, query, bind_vars=None):
        bv = bind_vars or {}
        value = bv.get("value")
        for field in ("slug", "filename"):
            if f"doc.{field} == @value" in query:
                return iter([d for d in self.docs.values() if d.get(field) == value][:1])
        return iter([])


def _viewset(method="PATCH", data=None, pk=None, db=None, is_admin=True):
    req = MagicMock(spec=Request)
    req.method = method
    req.data = data or {}
    req.user = MagicMock(is_authenticated=True, is_global_admin=is_admin)

    fake = db or _FakeViewDB([{"_key": "v1", "slug": "numerals-forms", "filename": "browse-numerals-forms.php",
                                "spec": VALID_SPEC, "schema_version": 1, "content": "<h1>old</h1>"}])
    mock_db = MagicMock()
    mock_db.collection.side_effect = fake.collection
    mock_db.aql.execute.side_effect = fake.aql_execute
    req.arangodb = mock_db

    vs = ViewViewSet()
    vs.request = req
    vs.kwargs = {"pk": pk} if pk else {}
    vs.format_kwarg = None
    return vs, fake


class ViewPartialUpdateTests(SimpleTestCase):

    def test_valid_spec_update_by_slug(self):
        vs, fake = _viewset(data={"spec": VALID_SPEC}, pk="numerals-forms")
        resp = vs.partial_update(vs.request, pk="numerals-forms")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(fake.docs["v1"]["spec"], VALID_SPEC)

    def test_invalid_spec_is_400_and_leaves_doc_unchanged(self):
        vs, fake = _viewset(data={"spec": {"schemaVersion": 999}}, pk="numerals-forms")
        original = dict(fake.docs["v1"])
        resp = vs.partial_update(vs.request, pk="numerals-forms")
        self.assertEqual(resp.status_code, 400)
        self.assertEqual(fake.docs["v1"], original)

    def test_no_editable_fields_is_400(self):
        vs, _ = _viewset(data={"content": "<h1>hacked</h1>"}, pk="numerals-forms")
        resp = vs.partial_update(vs.request, pk="numerals-forms")
        self.assertEqual(resp.status_code, 400)

    def test_content_field_cannot_be_written(self):
        vs, fake = _viewset(data={"spec": VALID_SPEC, "content": "<h1>hacked</h1>"}, pk="numerals-forms")
        vs.partial_update(vs.request, pk="numerals-forms")
        self.assertEqual(fake.docs["v1"]["content"], "<h1>old</h1>")

    def test_missing_view_is_404(self):
        vs, _ = _viewset(data={"spec": VALID_SPEC}, pk="does-not-exist")
        with self.assertRaises(NotFound):
            vs.partial_update(vs.request, pk="does-not-exist")

    def test_resolves_by_legacy_filename_too(self):
        vs, fake = _viewset(data={"spec": VALID_SPEC}, pk="browse-numerals-forms.php")
        resp = vs.partial_update(vs.request, pk="browse-numerals-forms.php")
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(fake.docs["v1"]["spec"], VALID_SPEC)


class ViewPermissionTests(SimpleTestCase):

    def _perms(self, method):
        vs = ViewViewSet()
        vs.request = MagicMock(method=method)
        return vs.get_permissions()

    def test_patch_requires_global_admin(self):
        perms = self._perms("PATCH")
        self.assertEqual(len(perms), 1)
        self.assertIsInstance(perms[0], IsGlobalAdmin)

    def test_get_is_public(self):
        perms = self._perms("GET")
        self.assertEqual(len(perms), 1)
        self.assertIsInstance(perms[0], AllowAny)
