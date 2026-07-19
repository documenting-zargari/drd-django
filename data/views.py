import csv
import io
import json
import os
import shutil
import uuid
from datetime import datetime

from django.conf import settings
from django.http import HttpResponse


def user_sees_hidden_samples(user):
    """True if the authenticated user has opted in to see non-visible samples."""
    return bool(
        user
        and user.is_authenticated
        and getattr(user, "is_global_admin", False)
        and getattr(user, "show_hidden_samples", False)
    )
from django.http import JsonResponse
from rest_framework import status
from rest_framework.decorators import action
from rest_framework.exceptions import NotFound, ValidationError
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.viewsets import ViewSet
from natsort import natsorted

from data.models import (
    Answer,
    Category,
    MasterPhrase,
    ResearchQuestion,
    Sample,
    SamplePhrase,
    Source,
    Transcription,
    View,
)
from data.serializers import (
    AnswerSerializer,
    CategorySerializer,
    MasterPhraseSerializer,
    PhraseSerializer,
    ResearchQuestionSerializer,
    SampleSerializer,
    SourceSerializer,
    TranscriptionSerializer,
    ViewSerializer,
)
from roma.views import ArangoModelViewSet
from user.permissions import CanEditSample, IsGlobalAdmin, IsGlobalOrProjectAdmin, IsProjectEditor


def _get_question_hierarchy_ids(db, question_id):
    """
    Return a ResearchQuestion's hierarchy_ids (itself plus every ancestor
    Category id), or None if not found.

    Used to match a MasterPhrase/Transcription's question_ids/category_ids
    against the specific question an answer belongs to: a direct hit on
    question_ids, or an overlap between category_ids and hierarchy_ids
    (the phrase/transcription was linked at a category level that covers
    this question). See extract/master_phrases_migration/PLAN.md — this
    replaces the old tag_ids-intersection approach entirely; tag_word
    text-search fallback is not carried over (tag_word was never meant to
    link phrases, per extract/Tags.md).
    """
    if question_id is None:
        return None
    cursor = db.aql.execute(
        "FOR q IN ResearchQuestions FILTER q.id == @id RETURN q.hierarchy_ids",
        bind_vars={"id": question_id},
    )
    return next(cursor, None)


class CategoryViewSet(ArangoModelViewSet):
    """
    API endpoint for browsing categories in a hierarchical structure.

    Categories are organized in a tree structure with parent-child relationships.
    Use the parent_id parameter to navigate the hierarchy.

    Available endpoints:
    - GET /categories/ - List root categories (parent_id=1 by default)
    - GET /categories/?parent_id=<id> - List child categories
    - GET /categories/<id>/ - Retrieve specific category
    - GET /categories/batch/?ids=<id1,id2,...> - Retrieve multiple categories by ID
    - GET /categories/search/?q=<term> - Search categories by name
    """

    model = Category
    serializer_class = CategorySerializer
    http_method_names = ["get", "head", "options"]  # prevent post

    def get_queryset(self):
        parent_id = self.request.query_params.get("parent_id")
        db = self.request.arangodb
        collection = db.collection(self.model.collection_name)
        exclude_ids = [2, 3]
        id = int(parent_id) if parent_id else 1
        categories_cursor = collection.find({"parent_id": id})
        return [c for c in categories_cursor if c["id"] not in exclude_ids]


    def list(self, request, *args, **kwargs):
        queryset = self.get_queryset()
        serializer = self.serializer_class(
            queryset, many=True, context={"request": request, "view": self}
        )
        return Response(serializer.data)

    def retrieve(self, request, pk=None):
        """
        Retrieve a specific category by ID.

        Parameters:
        - pk: Category ID (integer)

        Example:
        - /categories/5/ - Retrieves category with ID 5

        Returns full category details including hierarchy information.
        """
        instance = self.get_object(pk)
        serializer = self.serializer_class(
            instance, context={"request": request, "view": self}
        )
        return Response(serializer.data)

    @action(detail=False, methods=["get"])
    def search(self, request):
        """
        Search categories by name using pattern matching.

        Query Parameters:
        - q (required): Search term (minimum 2 characters)

        Example:
        - /categories/search/?q=music - Searches for categories containing 'music'

        Returns matching categories with hierarchy information, sorted by ID.
        Case-insensitive search using regular expressions.
        """
        query = request.query_params.get("q", "").strip()
        if not query or len(query) < 2:
            return Response([])

        db = request.arangodb
        if not db:
            return Response({"error": "Database not available"}, status=500)

        search_pattern = f".*{query}.*"
        aql_query = """
        FOR doc IN Categories
        FILTER REGEX_TEST(doc.name, @search_pattern, 'i')
        SORT doc.id ASC
        RETURN {
            "id": doc.id,
            "name": doc.name,
            "hierarchy": doc.hierarchy,
            "parent_id": doc.parent_id,
            "is_leaf": doc.is_leaf,
        }
        """

        try:
            cursor = db.aql.execute(
                aql_query, bind_vars={"search_pattern": search_pattern}
            )
            results = []
            for doc in cursor:
                # Parse hierarchy if it's stored as string
                hierarchy = doc.get("hierarchy", [])
                if isinstance(hierarchy, str):
                    try:
                        hierarchy = eval(hierarchy)
                    except Exception as _:
                        hierarchy = []
                results.append(
                    {
                        "id": doc["id"],
                        "name": doc["name"],
                        "hierarchy": hierarchy if len(hierarchy) > 2 else [],
                        "parent_id": doc["parent_id"],
                        "has_children": not doc.get("is_leaf", False),
                    }
                )

            return Response(results)
        except Exception as e:
            return Response({"error": f"Search failed: {str(e)}"}, status=500)

    @action(detail=False, methods=["get"])
    def batch(self, request):
        """
        Retrieve multiple categories by ID in a single request.

        Query Parameters:
        - ids (required): Comma-separated category IDs

        Example:
        - /categories/batch/?ids=10,20,30
        """
        ids_param = request.query_params.get("ids", "").strip()
        if not ids_param:
            return Response([])

        try:
            ids = [int(x) for x in ids_param.split(",") if x.strip()]
        except ValueError:
            return Response({"error": "ids must be comma-separated integers"}, status=400)

        if not ids:
            return Response([])

        db = request.arangodb
        aql_query = """
        FOR doc IN Categories
            FILTER doc.id IN @ids
            RETURN doc
        """
        cursor = db.aql.execute(aql_query, bind_vars={"ids": ids})
        results = list(cursor)
        serializer = self.serializer_class(
            results, many=True, context={"request": request, "view": self}
        )
        return Response(serializer.data)

    @action(detail=False, methods=["get"], url_path="search-views")
    def search_views(self, request):
        """
        Return all categories that have an associated view (path field).
        Optionally filter by name with ?q= parameter.
        """
        db = request.arangodb
        if not db:
            return Response({"error": "Database not available"}, status=500)

        query = request.query_params.get("q", "").strip()
        if query:
            search_pattern = f".*{query}.*"
            aql_query = """
            FOR doc IN Categories
            FILTER doc.path != null AND doc.path != "" AND REGEX_TEST(doc.name, @search_pattern, 'i')
            SORT doc.id ASC
            RETURN { "id": doc.id, "name": doc.name, "hierarchy": doc.hierarchy, "parent_id": doc.parent_id, "path": doc.path }
            """
            bind_vars = {"search_pattern": search_pattern}
        else:
            aql_query = """
            FOR doc IN Categories
            FILTER doc.path != null AND doc.path != ""
            SORT doc.id ASC
            RETURN { "id": doc.id, "name": doc.name, "hierarchy": doc.hierarchy, "parent_id": doc.parent_id, "path": doc.path }
            """
            bind_vars = {}

        try:
            cursor = db.aql.execute(aql_query, bind_vars=bind_vars)
            results = []
            for doc in cursor:
                hierarchy = doc.get("hierarchy", [])
                if isinstance(hierarchy, str):
                    try:
                        hierarchy = eval(hierarchy)
                    except Exception as _:
                        hierarchy = []
                results.append(
                    {
                        "id": doc["id"],
                        "name": doc["name"],
                        "hierarchy": hierarchy if len(hierarchy) > 2 else [],
                        "parent_id": doc["parent_id"],
                        "path": doc["path"],
                        "has_children": False,
                    }
                )

            return Response(results)
        except Exception as e:
            return Response({"error": f"Search failed: {str(e)}"}, status=500)


class ResearchQuestionViewSet(ArangoModelViewSet):
    """
    Read-only API endpoint for browsing/searching ResearchQuestions.

    Added for the MasterPhrases migration — editors need to look up and
    link specific research questions on MasterPhrases.question_ids (see
    extract/master_phrases_migration/PLAN.md). Deliberately minimal: no
    tree navigation like CategoryViewSet, just search-by-name and
    batch-by-id (for resolving a phrase's linked question_ids to
    human-readable labels).

    Available endpoints:
    - GET /research-questions/batch/?ids=<id1,id2,...> - Retrieve multiple questions by ID
    - GET /research-questions/search/?q=<term> - Search questions by name (min 2 chars)
    """

    model = ResearchQuestion
    serializer_class = ResearchQuestionSerializer
    http_method_names = ["get", "head", "options"]

    @action(detail=False, methods=["get"])
    def batch(self, request):
        """
        GET /research-questions/batch/?ids=<id1,id2,...> - retrieve multiple
        ResearchQuestions by id in a single request.
        """
        ids_param = request.query_params.get("ids", "").strip()
        if not ids_param:
            return Response([])

        try:
            ids = [int(x) for x in ids_param.split(",") if x.strip()]
        except ValueError:
            return Response({"error": "ids must be comma-separated integers"}, status=400)

        if not ids:
            return Response([])

        db = request.arangodb
        cursor = db.aql.execute(
            "FOR q IN ResearchQuestions FILTER q.id IN @ids RETURN q",
            bind_vars={"ids": ids},
        )
        results = list(cursor)
        serializer = self.serializer_class(results, many=True, context={"request": request, "view": self})
        return Response(serializer.data)

    @action(detail=False, methods=["get"])
    def search(self, request):
        """
        GET /research-questions/search/?q=<term> - search ResearchQuestions
        by name (case-insensitive substring, minimum 2 characters), sorted
        by id, capped at 50 results.
        """
        query = request.query_params.get("q", "").strip()
        if not query or len(query) < 2:
            return Response([])

        db = request.arangodb
        cursor = db.aql.execute(
            """
            FOR q IN ResearchQuestions
                FILTER REGEX_TEST(q.name, @pattern, true)
                SORT q.id
                LIMIT 50
                RETURN q
            """,
            bind_vars={"pattern": f".*{query}.*"},
        )
        results = list(cursor)
        serializer = self.serializer_class(results, many=True, context={"request": request, "view": self})
        return Response(serializer.data)


class PhraseViewSet(ArangoModelViewSet):
    """
    API endpoint for retrieving sample-level phrase recordings.

    Each phrase recording is a join of SamplePhrases (per-sample text,
    recording flag) and MasterPhrases (english, conjugated, question_ids,
    category_ids — shared across every sample of that phrase_ref). Replaces
    the old flat Phrases collection; see
    extract/master_phrases_migration/PLAN.md.

    Available endpoints:
    - GET /phrases/?sample=<sample_ref> - List phrases for a specific sample
    - GET /phrases/list/ - Unique phrase list for the phrase picker (phrase_ref + english only)
    - GET /phrases/<id>/ - Retrieve specific phrase by ID
    - GET /phrases/by-answer/?answer_key=<key> - Get phrases linked to an answer's research question
    - POST /phrases/search/ - Search phrases (see that endpoint for parameters)
    - POST /phrases/export/ - Export matching phrases without pagination (see that endpoint for parameters)

    Editing: PATCH here only changes the per-sample `phrase` text. To edit
    english/conjugated/question_ids/category_ids (shared across every
    sample of this phrase_ref), use MasterPhraseViewSet
    (/master-phrases/{phrase_ref}/) instead.

    Query Parameters:
    - sample (required for list): Sample reference (e.g., sample=AL-001)
    - answer_key (required for by-answer): Answer _key

    Examples:
    - /phrases/?sample=AL-001 - Phrases for sample AL-001
    - /phrases/list/ - Unique phrase list for picker (phrase_ref + english only)
    - /phrases/123/ - Specific phrase with ID 123
    - /phrases/by-answer/?answer_key=ABC123 - Phrases linked to answer ABC123 via its research question
    """

    model = SamplePhrase
    serializer_class = PhraseSerializer
    http_method_names = ["get", "post", "patch", "head", "options"]
    permission_classes = [AllowAny]  # GET and search POST are public; PATCH uses per-action override

    EDITABLE_FIELDS = {"phrase", "question_overrides"}

    # Resolved question_ids for display/matching: the MasterPhrase's own
    # links, plus this SamplePhrase's question_overrides.include, minus its
    # question_overrides.exclude. Requires `m` (MasterPhrase doc) and `sp`
    # (SamplePhrase doc) bound in the enclosing AQL FOR loop.
    RESOLVED_QUESTION_IDS_AQL = (
        "MINUS(UNION_DISTINCT((m.question_ids || []), (sp.question_overrides.include || [])), "
        "(sp.question_overrides.exclude || []))"
    )

    def get_permissions(self):
        if self.request.method == "PATCH":
            return [CanEditSample()]
        return [AllowAny()]

    def get_sample_ref(self, request):
        """Required by CanEditSample to resolve the target sample."""
        pk = self.kwargs.get("pk")
        if not pk:
            return None
        db = request.arangodb
        doc = db.collection(self.model.collection_name).get(pk)
        return doc.get("sample") if doc else None

    @staticmethod
    def _merge_with_master(db, sample_phrase):
        """english/conjugated only — question_ids/category_ids are
        deliberately excluded here too; fetch those via GET
        /phrases/{key}/links/ instead (see that action's docstring)."""
        master = db.collection(MasterPhrase.collection_name).get(sample_phrase["phrase_ref"])
        if not master:
            return sample_phrase
        return {
            **sample_phrase,
            "english": master.get("english"),
            "conjugated": master.get("conjugated"),
        }

    @staticmethod
    def _resolve_question_ids_for_sample_phrase(master, sample_phrase):
        overrides = sample_phrase.get("question_overrides") or {}
        master_question_ids = (master or {}).get("question_ids") or []
        return sorted(
            (set(master_question_ids) | set(overrides.get("include") or []))
            - set(overrides.get("exclude") or [])
        )

    @staticmethod
    def _validate_question_overrides(value):
        if value is None:
            return {"include": [], "exclude": []}
        if not isinstance(value, dict):
            raise ValidationError("question_overrides must be an object with include/exclude arrays")
        include = value.get("include") or []
        exclude = value.get("exclude") or []
        if not all(isinstance(v, int) for v in include) or not all(isinstance(v, int) for v in exclude):
            raise ValidationError("question_overrides.include/exclude must be arrays of research question ids")
        return {"include": sorted(set(include)), "exclude": sorted(set(exclude))}

    def partial_update(self, request, pk=None):
        """
        PATCH /phrases/{sample}_{phrase_ref}/ — update the per-sample phrase
        text and/or its question_overrides (rare, sample-scoped exceptions
        to the MasterPhrase's linked research questions — see
        question_overrides docstring on RESOLVED_QUESTION_IDS_AQL / by_answer).
        Requires editor+ role. Editors with sample restrictions may only
        edit phrases belonging to their allowed samples.

        Allowed fields: phrase, question_overrides
        """
        db = request.arangodb
        doc = db.collection(self.model.collection_name).get(pk)
        if not doc:
            raise NotFound(detail="Phrase not found")

        updates = {k: v for k, v in request.data.items() if k in self.EDITABLE_FIELDS}
        if not updates:
            return Response(
                {"error": f"No editable fields provided. Allowed: {sorted(self.EDITABLE_FIELDS)}"},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if "question_overrides" in updates:
            updates["question_overrides"] = self._validate_question_overrides(updates["question_overrides"])

        db.collection(self.model.collection_name).update({"_key": pk, **updates})
        updated = self._merge_with_master(db, db.collection(self.model.collection_name).get(pk))
        serializer = self.serializer_class(updated, context={"request": request})
        return Response(serializer.data)

    @action(detail=True, methods=["get"], url_path="links")
    def links(self, request, pk=None):
        """
        GET /phrases/{key}/links/ — the linking data omitted from list/
        search/by-answer/by-category/related responses (bulky: averages
        ~56 question_ids per phrase, unused by any bulk-list consumer).
        Fetch this for one phrase on demand, e.g. when an edit modal opens
        and needs to display/edit its linked research questions.

        Returns: { question_ids, category_ids, question_overrides }
        question_ids is RESOLVED: the MasterPhrase's own links, plus this
        SamplePhrase's question_overrides.include, minus its exclude —
        same computation as RESOLVED_QUESTION_IDS_AQL elsewhere in this file.
        """
        db = request.arangodb
        sample_phrase = db.collection(self.model.collection_name).get(pk)
        if not sample_phrase:
            raise NotFound(detail="Phrase not found")

        master = db.collection(MasterPhrase.collection_name).get(sample_phrase["phrase_ref"])
        overrides = sample_phrase.get("question_overrides") or {}
        return Response({
            "question_ids": self._resolve_question_ids_for_sample_phrase(master, sample_phrase),
            "category_ids": (master or {}).get("category_ids") or [],
            "question_overrides": {
                "include": overrides.get("include") or [],
                "exclude": overrides.get("exclude") or [],
            },
        })

    def get_queryset(self):
        try:
            sample = self.request.query_params.get("sample")
            if not sample:
                raise NotFound(detail="Sample parameter is required to fetch phrases")

            db = self.request.arangodb
            # question_ids/category_ids deliberately omitted here — they're
            # bulky (avg ~56 ints/phrase) and unused by any list/search
            # consumer; fetch them on demand via GET /phrases/{key}/links/
            # when an edit modal actually needs them for one phrase.
            aql = """
                FOR sp IN SamplePhrases
                    FILTER sp.sample == @sample
                    LET m = DOCUMENT(CONCAT("MasterPhrases/", sp.phrase_ref))
                    RETURN MERGE(sp, {
                        english: m.english,
                        conjugated: m.conjugated
                    })
            """
            cursor = db.aql.execute(aql, bind_vars={"sample": sample})
            return natsorted(list(cursor), key=lambda x: x["phrase_ref"])

        except NotFound:
            raise
        except Exception as e:
            print(f"Error fetching phrases: {e}")
            return []

    @action(detail=False, methods=["get"], url_path="list")
    def phrase_list(self, request):
        """
        GET /phrases/list/ — unique phrases for the phrase picker.
        One record per MasterPhrase (phrase_ref + english), sorted naturally by phrase_ref.
        """
        db = request.arangodb
        aql = """
            FOR m IN MasterPhrases
                RETURN { phrase_ref: m.phrase_ref, english: m.english }
        """
        cursor = db.aql.execute(aql)
        results = natsorted(list(cursor), key=lambda x: x["phrase_ref"])
        return Response(results)


    @action(detail=False, methods=["get"], url_path="by-answer")
    def by_answer(self, request):
        """
        Get phrases associated with an answer via its research question.

        Query Parameters:
        - answer_key (required): Answer _key

        Returns phrases where phrase.sample == answer.sample AND the
        MasterPhrase links to the answer's question (directly via
        question_ids, or via a covering category whose subtree includes
        this question — category_ids intersects the question's
        hierarchy_ids).

        If the answer has `phrase_overrides.include` set (non-empty), that
        list of phrase_refs is used instead of the question/category match
        entirely — this preserves exact per-answer precision for the ~65
        "heterogeneous" questions where different answers intentionally
        carry different tags (see extract/tag_divergence.md), now that
        matching happens per-question rather than per-answer.
        `phrase_overrides.exclude` is always subtracted, whichever path was
        used. See extract/master_phrases_migration/PLAN.md.

        On top of that, each matching SamplePhrase's own `question_overrides`
        is applied: a phrase whose `question_overrides.exclude` contains this
        answer's question_id is dropped even if the branch above matched it;
        a phrase (in this sample) whose `question_overrides.include` contains
        this answer's question_id is added even if the branch above didn't
        match it. This is the rare, sample-scoped exception a sample editor
        can make from the phrase editor — distinct from Answer.phrase_overrides
        above, which is question-scoped and admin/meta-editor territory.
        """
        from rest_framework.exceptions import ValidationError

        try:
            answer_key = request.query_params.get("answer_key")
            if not answer_key:
                raise ValidationError("Answer key parameter is required")

            db = request.arangodb
            answer = db.collection("Answers").get(answer_key)
            if not answer:
                raise NotFound(detail="Answer not found")

            sample = answer.get('sample')
            if not sample:
                return Response([])

            question_id = answer.get('question_id')
            overrides = answer.get('phrase_overrides') or {}
            include = overrides.get('include') or []
            exclude = overrides.get('exclude') or []
            return self._by_answer_response(db, sample, question_id, include, exclude, request)

        except (NotFound, ValidationError):
            raise
        except Exception as e:
            print(f"Error fetching phrases for answer: {e}")
            raise NotFound(detail="Error retrieving phrases")

    def _phrase_override_includes(self, db, category_id, sample, exclude):
        """SamplePhrases in this sample that declare relevance to category_id
        via their own question_overrides.include, regardless of whether the
        MasterPhrase itself matches. Shared by _phrases_by_category and
        by_answer's Answer.phrase_overrides.include branch."""
        aql = """
            FOR sp IN SamplePhrases
                FILTER sp.sample == @sample
                FILTER @category_id IN (sp.question_overrides.include || [])
                FILTER sp.phrase_ref NOT IN @exclude
                LET m = DOCUMENT(CONCAT("MasterPhrases/", sp.phrase_ref))
                RETURN MERGE(sp, {
                    english: m.english,
                    conjugated: m.conjugated
                })
        """
        return list(db.aql.execute(aql, bind_vars={'sample': sample, 'category_id': category_id, 'exclude': exclude}))

    def _phrases_by_category(self, db, category_id, sample, exclude=None):
        """
        Phrases linked to a research question/category id, scoped to one
        sample — the shared core of both by_answer's question/category-match
        branch and the by_category action (see that action's docstring).

        Matches MasterPhrase.question_ids/category_ids covering category_id,
        joined to this sample's SamplePhrase, with SamplePhrase.question_overrides
        layered on top (exclude subtracts a phrase; include adds one even if
        the MasterPhrase wouldn't otherwise match). `exclude` is an optional
        list of phrase_refs to additionally drop (used by by_answer for its
        Answer.phrase_overrides.exclude).
        """
        exclude = exclude or []
        hierarchy_ids = _get_question_hierarchy_ids(db, category_id)
        if hierarchy_ids is None:
            return []

        aql = """
            FOR m IN MasterPhrases
                FILTER (@category_id IN (m.question_ids || [])
                        OR LENGTH(INTERSECTION(m.category_ids || [], @hierarchy_ids)) > 0)
                    AND m.phrase_ref NOT IN @exclude
                LET sp = DOCUMENT(CONCAT("SamplePhrases/", @sample, "_", m.phrase_ref))
                FILTER sp != null
                FILTER @category_id NOT IN (sp.question_overrides.exclude || [])
                RETURN MERGE(sp, {
                    english: m.english,
                    conjugated: m.conjugated
                })
        """
        phrases = list(db.aql.execute(aql, bind_vars={
            'category_id': category_id, 'hierarchy_ids': hierarchy_ids, 'sample': sample, 'exclude': exclude,
        }))

        overrides = self._phrase_override_includes(db, category_id, sample, exclude)
        seen_keys = {p['_key'] for p in phrases}
        for p in overrides:
            if p['_key'] not in seen_keys:
                phrases.append(p)
                seen_keys.add(p['_key'])
        return phrases

    def _phrases_by_explicit_refs(self, db, sample, category_id, include, exclude):
        """Phrases named directly by Answer.phrase_overrides.include (the
        ~65 divergent-question case), plus the same SamplePhrase-level
        include union _phrases_by_category applies."""
        aql = """
            FOR phrase_ref IN @include
                FILTER phrase_ref NOT IN @exclude
                LET sp = DOCUMENT(CONCAT("SamplePhrases/", @sample, "_", phrase_ref))
                FILTER sp != null
                FILTER @category_id NOT IN (sp.question_overrides.exclude || [])
                LET m = DOCUMENT(CONCAT("MasterPhrases/", phrase_ref))
                RETURN MERGE(sp, {
                    english: m.english,
                    conjugated: m.conjugated
                })
        """
        bind_vars = {'include': include, 'exclude': exclude, 'sample': sample, 'category_id': category_id}
        phrases = list(db.aql.execute(aql, bind_vars=bind_vars))
        overrides = self._phrase_override_includes(db, category_id, sample, exclude)
        seen_keys = {p['_key'] for p in phrases}
        for p in overrides:
            if p['_key'] not in seen_keys:
                phrases.append(p)
                seen_keys.add(p['_key'])
        return phrases

    def _resolve_phrases(self, db, sample, question_id, include, exclude):
        """Resolves the phrase list for a question_id: via
        Answer.phrase_overrides.include if set (exact per-answer precision
        for divergent questions), otherwise via _phrases_by_category.
        Shared by by_answer and RelatedContentViewSet."""
        # sp is a direct primary-key lookup (SamplePhrases._key ==
        # "{sample}_{phrase_ref}"), not a scan — avoids re-scanning all
        # 128k SamplePhrases once per matching MasterPhrase.
        if include:
            return self._phrases_by_explicit_refs(db, sample, question_id, include, exclude)
        return self._phrases_by_category(db, question_id, sample, exclude=exclude)

    def _by_answer_response(self, db, sample, question_id, include, exclude, request):
        """Shared response-building tail for by_answer: resolves, sorts,
        and serializes."""
        phrases = self._resolve_phrases(db, sample, question_id, include, exclude)
        if not phrases:
            return Response([])

        phrases = natsorted(phrases, key=lambda x: x.get("phrase_ref", ""))
        serializer = self.serializer_class(phrases, many=True, context={"request": request})
        return Response(serializer.data)

    @action(detail=False, methods=["get"], url_path="by-category")
    def by_category(self, request):
        """
        GET /phrases/by-category/?category_id=<id>&sample=<sample_ref>

        Same matching as by-answer's question/category branch, but keyed
        directly by a stable ResearchQuestion/Category id + sample instead
        of an Answer _key. Prefer this over by-answer when the caller
        already has the category_id in hand (e.g. Tables cell metadata) —
        category/question ids are stable across re-imports/migrations,
        whereas Answer._key is an ArangoDB-generated key that isn't.

        Does not apply Answer.phrase_overrides (there's no specific Answer
        in play here) — only the MasterPhrase/SamplePhrase-level matching
        and SamplePhrase.question_overrides.
        """
        from rest_framework.exceptions import ValidationError

        try:
            category_id = request.query_params.get("category_id")
            sample = request.query_params.get("sample")
            if not category_id:
                raise ValidationError("category_id parameter is required")
            if not sample:
                raise ValidationError("sample parameter is required")
            try:
                category_id = int(category_id)
            except (TypeError, ValueError):
                raise ValidationError("category_id must be an integer")

            db = request.arangodb
            phrases = self._phrases_by_category(db, category_id, sample)

            if not phrases:
                return Response([])

            phrases = natsorted(phrases, key=lambda x: x.get("phrase_ref", ""))
            serializer = self.serializer_class(phrases, many=True, context={"request": request})
            return Response(serializer.data)

        except (NotFound, ValidationError):
            raise
        except Exception as e:
            print(f"Error fetching phrases for category: {e}")
            raise NotFound(detail="Error retrieving phrases")

    @action(detail=False, methods=["post"], url_path="search")
    def search(self, request):
        """
        Search phrases across multiple samples.

        Request Body:
        - query (required unless phrase_ref given, min 2 chars): Free-text search term
        - phrase_ref (optional): Exact phrase reference (e.g. "80a"). When provided, bypasses
          full-text search and returns all samples' entries for that specific phrase via index lookup.
          query is not required when phrase_ref is given.
        - sample_refs (optional): List of sample refs to limit search
        - sort (optional, default 'phrase_ref'): Sort field — 'phrase_ref' or 'sample'
        - field (optional, default 'both'): Text search field — 'romani', 'english', or 'both'. Ignored when phrase_ref is given.
        - page (optional, default 1): Page number. Ignored when phrase_ref is given (all results returned).
        - page_size (optional, default 50, max 200): Results per page. Ignored when phrase_ref is given.
        """
        phrase_ref = request.data.get("phrase_ref", "").strip()
        query = request.data.get("query", "").strip()
        if not phrase_ref and (not query or len(query) < 2):
            raise ValidationError("Query must be at least 2 characters")

        sample_refs = request.data.get("sample_refs", [])
        sort = request.data.get("sort", "phrase_ref")
        field = request.data.get("field", "both")  # 'romani', 'english', or 'both'
        page = int(request.data.get("page", 1))
        page_size = min(int(request.data.get("page_size", 50)), 200)
        offset = (page - 1) * page_size

        db = request.arangodb

        # Natural sort for phrase_ref: extract leading number, then sort by that numerically,
        # then by the remaining suffix (e.g. "80a" → 80, "a")
        natsort_expr = "TO_NUMBER(REGEX_REPLACE(phrase.phrase_ref, '[^0-9].*$', '')), REGEX_REPLACE(phrase.phrase_ref, '^[0-9]+', '')"
        sort_clauses = {
            "phrase_ref": f"SORT {natsort_expr}, phrase.sample",
            "sample": f"SORT phrase.sample, {natsort_expr}",
        }
        sort_aql = sort_clauses.get(sort, sort_clauses["phrase_ref"])

        if phrase_ref:
            # Exact phrase_ref match via persistent index — no text search, no sample_refs resolution.
            # Visibility and sample label resolved per-row via inline Samples lookup.
            see_hidden = user_sees_hidden_samples(request.user)
            visibility_filter = "" if see_hidden else "FILTER s.visible == 'Yes'"
            sample_filter = "FILTER sp.sample IN @sample_refs" if sample_refs else ""
            bind: dict = {"phrase_ref": phrase_ref}
            if sample_refs:
                bind["sample_refs"] = sample_refs
            results_aql = f"""
                LET m = DOCUMENT(CONCAT("MasterPhrases/", @phrase_ref))
                FILTER m != null
                FOR sp IN SamplePhrases
                    FILTER sp.phrase_ref == m.phrase_ref
                    {sample_filter}
                    FOR s IN Samples
                        FILTER s.sample_ref == sp.sample
                        {visibility_filter}
                        LET phrase = MERGE(sp, {{
                            english: m.english,
                            conjugated: m.conjugated
                        }})
                        {sort_aql}
                        RETURN MERGE(phrase, {{
                            sample_label: CONCAT_SEPARATOR(', ', s.dialect_name, s.location)
                        }})
            """
            try:
                results = list(db.aql.execute(results_aql, bind_vars=bind))
                serializer = self.serializer_class(results, many=True, context={"request": request})
                return Response({"count": len(results), "page": 1, "page_size": len(results), "results": serializer.data})
            except Exception as e:
                print(f"Error searching phrases by phrase_ref: {e}")
                raise ValidationError(f"Search failed: {str(e)}")
        else:
            # Resolve sample refs upfront (once) instead of a subquery per phrase
            if not sample_refs:
                if user_sees_hidden_samples(request.user):
                    sample_refs = list(db.aql.execute("FOR s IN Samples RETURN s.sample_ref"))
                else:
                    sample_refs = list(db.aql.execute("FOR s IN Samples FILTER s.visible == 'Yes' RETURN s.sample_ref"))

            # Romani text ('phrase') lives per-sample on SamplePhrases, indexed by the
            # SamplePhraseSearch ArangoSearch view (norm_lower analyzer). English lives
            # once per phrase_ref on the small MasterPhrases collection (~1,100 docs) —
            # a plain LIKE scan there is cheap and avoids re-denormalizing english onto
            # every sample row just for search convenience.
            query_lower = query.lower()
            search_romani = field in ("romani", "both")
            search_english = field in ("english", "both")

            candidates_aql = """
                LET romani_keys = @search_romani ? (
                    FOR sp IN SamplePhraseSearch
                        SEARCH ANALYZER(LIKE(sp.phrase, CONCAT("%", @query, "%")), "norm_lower")
                        FILTER sp.sample IN @sample_refs
                        RETURN sp._key
                ) : []
                LET english_refs = @search_english ? (
                    FOR m IN MasterPhrases
                        FILTER LIKE(m.english, CONCAT("%", @query, "%"), true)
                        RETURN m.phrase_ref
                ) : []
                LET english_keys = @search_english ? (
                    FOR sp IN SamplePhrases
                        FILTER sp.phrase_ref IN english_refs AND sp.sample IN @sample_refs
                        RETURN sp._key
                ) : []
                LET candidate_keys = UNIQUE(APPEND(romani_keys, english_keys))
            """
            count_aql = f"""
                {candidates_aql}
                RETURN LENGTH(candidate_keys)
            """
            results_aql = f"""
                {candidates_aql}
                LET sample_lookup = (
                    FOR s IN Samples
                        RETURN {{ref: s.sample_ref, label: CONCAT_SEPARATOR(', ', s.dialect_name, s.location)}}
                )
                LET sample_map = ZIP(sample_lookup[*].ref, sample_lookup[*].label)
                FOR key IN candidate_keys
                    LET sp = DOCUMENT(CONCAT("SamplePhrases/", key))
                    LET m = DOCUMENT(CONCAT("MasterPhrases/", sp.phrase_ref))
                    LET phrase = MERGE(sp, {{
                        english: m.english,
                        conjugated: m.conjugated
                    }})
                    {sort_aql}
                    LIMIT @offset, @page_size
                    RETURN MERGE(phrase, {{
                        sample_label: sample_map[phrase.sample]
                    }})
            """
            bind = {
                "query": query_lower,
                "sample_refs": sample_refs,
                "search_romani": search_romani,
                "search_english": search_english,
            }
            count_bind = bind
            results_bind = {**bind, "offset": offset, "page_size": page_size}

        try:
            count_cursor = db.aql.execute(count_aql, bind_vars=count_bind)
            total = next(count_cursor, 0)

            results_cursor = db.aql.execute(results_aql, bind_vars=results_bind)
            results = list(results_cursor)

            serializer = self.serializer_class(
                results, many=True, context={"request": request}
            )

            return Response({
                "count": total,
                "page": page,
                "page_size": page_size,
                "results": serializer.data,
            })
        except Exception as e:
            print(f"Error searching phrases: {e}")
            raise ValidationError(f"Search failed: {str(e)}")

    @action(detail=False, methods=["post"], url_path="export")
    def export(self, request):
        """
        Export all matching phrases (no pagination) for download.

        Same parameters as search but always returns all results without pagination.
        - query (required unless phrase_ref given, min 2 chars): Free-text search term
        - phrase_ref (optional): Exact phrase reference (e.g. "80a"). When provided, exports
          all samples' entries for that specific phrase via index lookup.
        - sample_refs (optional): List of sample refs to limit results
        - sort (optional, default 'phrase_ref'): Sort field — 'phrase_ref' or 'sample'
        - field (optional, default 'both'): Text search field — 'romani', 'english', or 'both'. Ignored when phrase_ref is given.
        """
        phrase_ref = request.data.get("phrase_ref", "").strip()
        query = request.data.get("query", "").strip()
        if not phrase_ref and (not query or len(query) < 2):
            raise ValidationError("Query must be at least 2 characters")

        sample_refs = request.data.get("sample_refs", [])
        sort = request.data.get("sort", "phrase_ref")
        field = request.data.get("field", "both")

        db = request.arangodb

        natsort_expr = "TO_NUMBER(REGEX_REPLACE(phrase.phrase_ref, '[^0-9].*$', '')), REGEX_REPLACE(phrase.phrase_ref, '^[0-9]+', '')"
        sort_clauses = {
            "phrase_ref": f"SORT {natsort_expr}, phrase.sample",
            "sample": f"SORT phrase.sample, {natsort_expr}",
        }
        sort_aql = sort_clauses.get(sort, sort_clauses["phrase_ref"])

        export_fields = """
            RETURN {
                phrase_ref: phrase.phrase_ref,
                sample: phrase.sample,
                sample_label: sample_label,
                phrase: phrase.phrase,
                english: phrase.english,
                conjugated: phrase.conjugated,
                has_recording: phrase.has_recording
            }
        """

        if phrase_ref:
            see_hidden = user_sees_hidden_samples(request.user)
            visibility_filter = "" if see_hidden else "FILTER s.visible == 'Yes'"
            sample_filter = "FILTER sp.sample IN @sample_refs" if sample_refs else ""
            bind: dict = {"phrase_ref": phrase_ref}
            if sample_refs:
                bind["sample_refs"] = sample_refs
            export_aql = f"""
                LET m = DOCUMENT(CONCAT("MasterPhrases/", @phrase_ref))
                FILTER m != null
                FOR sp IN SamplePhrases
                    FILTER sp.phrase_ref == m.phrase_ref
                    {sample_filter}
                    FOR s IN Samples
                        FILTER s.sample_ref == sp.sample
                        {visibility_filter}
                        LET sample_label = CONCAT_SEPARATOR(', ', s.dialect_name, s.location)
                        LET phrase = MERGE(sp, {{
                            english: m.english,
                            conjugated: m.conjugated
                        }})
                        {sort_aql}
                        {export_fields}
            """
        else:
            if not sample_refs:
                if user_sees_hidden_samples(request.user):
                    sample_refs = list(db.aql.execute("FOR s IN Samples RETURN s.sample_ref"))
                else:
                    sample_refs = list(db.aql.execute("FOR s IN Samples FILTER s.visible == 'Yes' RETURN s.sample_ref"))

            query_lower = query.lower()
            search_romani = field in ("romani", "both")
            search_english = field in ("english", "both")
            bind = {
                "query": query_lower,
                "sample_refs": sample_refs,
                "search_romani": search_romani,
                "search_english": search_english,
            }
            export_aql = f"""
                LET romani_keys = @search_romani ? (
                    FOR sp IN SamplePhraseSearch
                        SEARCH ANALYZER(LIKE(sp.phrase, CONCAT("%", @query, "%")), "norm_lower")
                        FILTER sp.sample IN @sample_refs
                        RETURN sp._key
                ) : []
                LET english_refs = @search_english ? (
                    FOR m IN MasterPhrases
                        FILTER LIKE(m.english, CONCAT("%", @query, "%"), true)
                        RETURN m.phrase_ref
                ) : []
                LET english_keys = @search_english ? (
                    FOR sp IN SamplePhrases
                        FILTER sp.phrase_ref IN english_refs AND sp.sample IN @sample_refs
                        RETURN sp._key
                ) : []
                LET candidate_keys = UNIQUE(APPEND(romani_keys, english_keys))
                LET sample_lookup = (
                    FOR s IN Samples
                        RETURN {{ref: s.sample_ref, label: CONCAT_SEPARATOR(', ', s.dialect_name, s.location)}}
                )
                LET sample_map = ZIP(sample_lookup[*].ref, sample_lookup[*].label)
                FOR key IN candidate_keys
                    LET sp = DOCUMENT(CONCAT("SamplePhrases/", key))
                    LET m = DOCUMENT(CONCAT("MasterPhrases/", sp.phrase_ref))
                    LET sample_label = sample_map[sp.sample]
                    LET phrase = MERGE(sp, {{
                        english: m.english,
                        conjugated: m.conjugated
                    }})
                    {sort_aql}
                    {export_fields}
            """

        try:
            cursor = db.aql.execute(export_aql, bind_vars=bind)
            return Response(list(cursor))
        except Exception as e:
            print(f"Error exporting phrases: {e}")
            raise ValidationError(f"Export failed: {str(e)}")


class MasterPhraseViewSet(ArangoModelViewSet):
    """
    API endpoint for editing MasterPhrases — the fields shared across every
    sample recording of a given phrase_ref (english, conjugated,
    question_ids, category_ids). Read access to phrase data goes through
    PhraseViewSet (which joins SamplePhrases + MasterPhrases); this
    viewset exists only so edits to the shared fields have a clear,
    singular target rather than being smuggled into a per-sample PATCH.

    Available endpoints:
    - GET /master-phrases/{phrase_ref}/ - read english/conjugated/question_ids/category_ids
      for one phrase concept (public read — used by the admin "Edit Phrase
      Concept" modal, and cheaper than the old approach of denormalizing
      question_ids onto every bulk phrase list/search response)
    - PATCH /master-phrases/{phrase_ref}/ - update english/conjugated/question_ids/category_ids

    Editing a MasterPhrase affects every sample of that phrase at once, and
    defines the phrase concept itself (english gloss, which research
    questions/categories it answers) rather than one sample's transcription
    of it — this is meta-editor/superadmin territory (global admin), not the
    regular per-sample CanEditSample privilege used elsewhere. Sample
    editors make rare per-sample exceptions via SamplePhrase.question_overrides
    (see PhraseViewSet.partial_update) instead of editing this directly.
    """

    model = MasterPhrase
    serializer_class = MasterPhraseSerializer
    http_method_names = ["get", "patch", "head", "options"]

    EDITABLE_FIELDS = {"english", "conjugated", "question_ids", "category_ids"}

    def get_permissions(self):
        if self.request.method == "PATCH":
            return [IsGlobalAdmin()]
        return [AllowAny()]

    def partial_update(self, request, pk=None):
        """
        PATCH /master-phrases/{phrase_ref}/ — update fields shared across
        every sample's recording of this phrase.

        Allowed fields: english, conjugated, question_ids, category_ids
        """
        db = request.arangodb
        doc = db.collection(self.model.collection_name).get(pk)
        if not doc:
            raise NotFound(detail="MasterPhrase not found")

        updates = {k: v for k, v in request.data.items() if k in self.EDITABLE_FIELDS}
        if not updates:
            return Response(
                {"error": f"No editable fields provided. Allowed: {sorted(self.EDITABLE_FIELDS)}"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        db.collection(self.model.collection_name).update({"_key": pk, **updates})
        updated = db.collection(self.model.collection_name).get(pk)
        serializer = self.serializer_class(updated, context={"request": request})
        return Response(serializer.data)


class SampleViewSet(ArangoModelViewSet):
    """
    API endpoint for retrieving linguistic samples.

    Samples are audio/text recordings with metadata. Only visible samples
    are returned in listings.

    Available endpoints:
    - GET /samples/ - List all visible samples
    - GET /samples/<sample_ref>/ - Retrieve specific sample by reference
    - GET /samples/with-transcriptions/ - List samples that have transcriptions with counts

    Samples are identified by their sample_ref (not numeric ID).
    """

    serializer_class = SampleSerializer
    model = Sample
    http_method_names = ["get", "post", "patch", "delete", "head", "options"]

    EDITABLE_FIELDS = {
        "dialect_name", "self_attrib_name", "dialect_group_name",
        "location", "country_code", "coordinates", "visible",
        "migrant", "contact_languages", "annotations",
    }

    def get_permissions(self):
        if self.request.method == "PATCH":
            return [IsProjectEditor()]
        return [AllowAny()]

    def partial_update(self, request, pk=None):
        """
        PATCH /samples/{sample_ref}/ — update editable metadata fields.
        Requires editor+ role.

        Allowed fields: dialect_name, self_attrib_name, dialect_group_name,
        location, country_code, coordinates, visible, migrant, contact_languages
        """
        db = request.arangodb
        # Look up by sample_ref
        cursor = db.collection(self.model.collection_name).find({"sample_ref": pk}, limit=1)
        docs = list(cursor)
        if not docs:
            raise NotFound(detail="Sample not found")
        doc = docs[0]

        updates = {k: v for k, v in request.data.items() if k in self.EDITABLE_FIELDS}
        if not updates:
            return Response(
                {"error": f"No editable fields provided. Allowed: {sorted(self.EDITABLE_FIELDS)}"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        if "annotations" in updates:
            ann = updates["annotations"]
            if not isinstance(ann, dict):
                return Response({"error": "annotations must be an object"}, status=status.HTTP_400_BAD_REQUEST)
            for k, v in ann.items():
                if not isinstance(k, str) or not isinstance(v, str):
                    return Response({"error": "annotation keys and values must be strings"}, status=status.HTTP_400_BAD_REQUEST)

        db.collection(self.model.collection_name).update({"_key": doc["_key"], **updates}, merge=False)
        updated_cursor = db.aql.execute("""
            FOR sample IN Samples
            FILTER sample.sample_ref == @sample_ref
            LET sources = (FOR source IN Sources FILTER source.sample == sample.sample_ref RETURN source)
            RETURN MERGE(sample, {sources: sources})
        """, bind_vars={"sample_ref": pk})
        updated = next(updated_cursor, None)
        serializer = self.serializer_class(updated, context={"request": request})
        return Response(serializer.data)

    def get_object(self, pk):
        # Override to use sample_ref and include sources
        db = self.request.arangodb
        aql_query = """
        FOR sample IN Samples
        FILTER sample.sample_ref == @sample_ref
        LET sources = (FOR source IN Sources FILTER source.sample == sample.sample_ref RETURN source)
        RETURN MERGE(sample, {sources: sources})
        """
        cursor = db.aql.execute(aql_query, bind_vars={"sample_ref": pk})
        docs = list(cursor)
        if not docs:
            raise NotFound(detail="Sample not found")
        return docs[0]

    def get_queryset(self):
        try:
            db = self.request.arangodb
            collection = db.collection(self.model.collection_name)
            if user_sees_hidden_samples(self.request.user):
                cursor = collection.all()
            else:
                cursor = collection.find({"visible": "Yes"})
            return [sample for sample in cursor]
        except Exception:
            return []

    def create(self, request):
        return JsonResponse({"error": "Method not allowed"}, status=405)

    @action(detail=False, methods=["get"], url_path="with-transcriptions")
    def with_transcriptions(self, request):
        """
        Get all sample references that have transcriptions with counts.

        Example:
        - /samples/with-transcriptions/ - List samples with transcription counts

        Returns:
        [
            {"sample_ref": "AL-001", "transcription_count": 15},
            {"sample_ref": "AT-001x", "transcription_count": 8},
            ...
        ]

        This endpoint efficiently returns sample references and their transcription
        counts using a single optimized AQL query with COLLECT operation.
        """
        db = request.arangodb
        if not db:
            return Response({"error": "Database not available"}, status=500)

        aql_query = """
        FOR transcription IN Transcriptions
            COLLECT sample = transcription.sample WITH COUNT INTO count
            SORT sample ASC
            RETURN {
                sample_ref: sample,
                transcription_count: count
            }
        """

        try:
            cursor = db.aql.execute(aql_query)
            return Response([result for result in cursor])
        except Exception as e:
            return Response({"error": f"Query failed: {str(e)}"}, status=500)

    @action(detail=False, methods=["get"], url_path="check")
    def check_sample_ref(self, request):
        """
        GET /samples/check/?ref=XY-042 — check whether a sample_ref exists and return
        its metadata and phrase count. Admin only.
        """
        if not IsGlobalOrProjectAdmin().has_permission(request, self):
            return Response({"error": "Admin access required"}, status=403)

        ref = (request.query_params.get("ref") or "").strip()
        if not ref:
            return Response({"error": "ref parameter is required"}, status=400)

        db = request.arangodb
        cursor = list(db.collection("Samples").find({"sample_ref": ref}, limit=1))
        if not cursor:
            return Response({"exists": False, "phrase_count": 0})

        sample = cursor[0]
        phrase_count = next(db.aql.execute(
            "FOR p IN Phrases FILTER p.sample == @s COLLECT WITH COUNT INTO n RETURN n",
            bind_vars={"s": ref},
        ), 0)

        arango_internal = {"_id", "_key", "_rev"}
        sample_data = {k: v for k, v in sample.items() if k not in arango_internal}
        return Response({"exists": True, "phrase_count": phrase_count, "sample": sample_data})

    @action(detail=False, methods=["get"], url_path="import-template")
    def import_template(self, request):
        """
        GET /samples/import-template/ — download a CSV pre-filled with all canonical phrase_refs.
        Admin only.
        """
        if not IsGlobalOrProjectAdmin().has_permission(request, self):
            return Response({"error": "Admin access required"}, status=403)

        db = request.arangodb
        cursor = db.aql.execute("""
            FOR p IN Phrases
                COLLECT ref = p.phrase_ref INTO g
                LET doc = FIRST(g[*].p)
                RETURN { phrase_ref: doc.phrase_ref, english: doc.english }
        """)
        phrases = natsorted(list(cursor), key=lambda x: x["phrase_ref"])

        output = io.BytesIO()
        wrapper = io.TextIOWrapper(output, encoding="utf-8-sig", newline="")
        writer = csv.writer(wrapper)
        writer.writerow(["phrase_ref", "english", "phrase", "conjugated"])
        for p in phrases:
            writer.writerow([p.get("phrase_ref", ""), p.get("english", ""), "", ""])
        wrapper.flush()

        response = HttpResponse(output.getvalue(), content_type="text/csv; charset=utf-8")
        response["Content-Disposition"] = 'attachment; filename="sample_import_template.csv"'
        return response

    @action(detail=False, methods=["post"], url_path="import")
    def import_sample(self, request):
        """
        POST /samples/import/ — create a new sample from a CSV file + metadata form fields.
        Full validation pass before any writes; rejects with per-row errors on failure.
        Admin only.
        """
        if not IsGlobalOrProjectAdmin().has_permission(request, self):
            return Response({"error": "Admin access required"}, status=403)

        sample_ref = (request.data.get("sample_ref") or "").strip()
        if not sample_ref:
            return Response({"error": "sample_ref is required"}, status=400)

        db = request.arangodb

        try:
            existing = list(db.collection("Samples").find({"sample_ref": sample_ref}, limit=1))
        except Exception as exc:
            return Response({"error": f"Database error checking sample_ref: {exc}"}, status=500)

        upgrade = request.data.get("upgrade") in ("true", "1", "yes")

        if existing and not upgrade:
            try:
                existing_phrase_count = next(db.aql.execute(
                    "FOR p IN Phrases FILTER p.sample == @s COLLECT WITH COUNT INTO n RETURN n",
                    bind_vars={"s": sample_ref},
                ), 0)
            except Exception as exc:
                return Response({"error": f"Database error checking existing phrases: {exc}"}, status=500)

            if existing_phrase_count == 0:
                upgrade = True  # sample shell exists but has no phrases — proceed silently
            else:
                return Response({
                    "exists": True,
                    "existing_phrase_count": existing_phrase_count,
                }, status=400)

        csv_file = request.FILES.get("file")
        if not csv_file:
            return Response({"error": "CSV file is required"}, status=400)

        raw = csv_file.read()
        for encoding in ("utf-8-sig", "utf-8", "cp1252", "latin-1"):
            try:
                text = raw.decode(encoding)
                break
            except (UnicodeDecodeError, LookupError):
                continue

        try:
            try:
                dialect = csv.Sniffer().sniff(text[:4096], delimiters=",;\t|")
            except csv.Error:
                dialect = csv.excel
            reader = csv.DictReader(io.StringIO(text), dialect=dialect)
            rows = list(reader)
            fieldnames = reader.fieldnames or []
        except Exception as exc:
            return Response({"error": f"Could not parse CSV: {exc}"}, status=400)

        if not fieldnames:
            return Response({"error": "CSV file appears to be empty or has no header row"}, status=400)

        required_cols = {"phrase_ref", "phrase"}
        missing = required_cols - set(fieldnames)
        if missing:
            return Response({
                "error": f"CSV is missing required column(s): {', '.join(sorted(missing))}. "
                         f"Found columns: {', '.join(fieldnames)}"
            }, status=400)

        try:
            canonical_refs = set(db.aql.execute(
                "FOR p IN Phrases COLLECT ref = p.phrase_ref RETURN ref"
            ))
        except Exception as exc:
            return Response({"error": f"Database error loading phrase references: {exc}"}, status=500)

        errors = []
        phrases_to_create = []

        for i, row in enumerate(rows, start=2):
            phrase_ref = (row.get("phrase_ref") or "").strip()
            phrase = (row.get("phrase") or "").strip()
            english = (row.get("english") or "").strip()
            conjugated_raw = (row.get("conjugated") or "").strip().lower()

            if not phrase_ref:
                errors.append({"row": i, "phrase_ref": "", "message": "phrase_ref is empty"})
                continue
            if phrase_ref not in canonical_refs:
                errors.append({
                    "row": i,
                    "phrase_ref": phrase_ref,
                    "message": f"phrase_ref '{phrase_ref}' does not exist in the canonical phrase list",
                })
                continue
            if not phrase:
                if request.data.get("skip_empty") in ("true", "1", "yes"):
                    continue
                errors.append({
                    "row": i,
                    "phrase_ref": phrase_ref,
                    "message": "phrase column is empty — Romani text is required (or enable 'Skip empty phrases')",
                })
                continue

            conjugated = None
            if conjugated_raw in ("y", "yes", "true", "1"):
                conjugated = True
            elif conjugated_raw in ("n", "no", "false", "0"):
                conjugated = False
            elif conjugated_raw:
                errors.append({
                    "row": i,
                    "phrase_ref": phrase_ref,
                    "message": f"conjugated value '{row.get('conjugated', '')}' is not recognised — use Y, N, or leave blank",
                })
                continue

            phrases_to_create.append({
                "phrase_ref": phrase_ref,
                "phrase": phrase,
                "english": english,
                "conjugated": conjugated,
                "sample": sample_ref,
            })

        if errors:
            return Response({"errors": errors}, status=400)

        skipped_empty_count = len(rows) - len(phrases_to_create) - len(errors)

        if not phrases_to_create:
            return Response({"error": "CSV contains no phrase rows after the header"}, status=400)

        batch_id = str(uuid.uuid4())
        now = datetime.utcnow().isoformat()

        # ── Create mode ──────────────────────────────────────────────────────
        if not upgrade:
            sample_doc = {
                "sample_ref": sample_ref,
                "dialect_name": (request.data.get("dialect_name") or "").strip(),
                "self_attrib_name": (request.data.get("self_attrib_name") or "").strip(),
                "dialect_group_name": (request.data.get("dialect_group_name") or "").strip(),
                "location": (request.data.get("location") or "").strip(),
                "country_code": (request.data.get("country_code") or "").strip(),
                "visible": request.data.get("visible", "No"),
                "migrant": request.data.get("migrant", "No"),
                "source_type": (request.data.get("source_type") or "").strip(),
                "import_batch_id": batch_id,
            }
            try:
                db.collection("Samples").insert(sample_doc)
            except Exception as exc:
                return Response({"error": f"Failed to insert sample document: {exc}"}, status=500)

            for p in phrases_to_create:
                p["import_batch_id"] = batch_id

            try:
                db.collection("Phrases").insert_many(phrases_to_create)
            except Exception as exc:
                try:
                    db.aql.execute(
                        "FOR s IN Samples FILTER s.import_batch_id == @bid REMOVE s IN Samples",
                        bind_vars={"bid": batch_id},
                    )
                except Exception:
                    pass
                return Response({"error": f"Failed to insert phrases: {exc}"}, status=500)

            inserted_count = len(phrases_to_create)
            updated_count = 0
            rollback_updates = []

        # ── Upgrade mode ─────────────────────────────────────────────────────
        else:
            # Load existing phrases for this sample keyed by phrase_ref
            try:
                existing_cursor = db.aql.execute(
                    "FOR p IN Phrases FILTER p.sample == @s "
                    "RETURN {phrase_ref: p.phrase_ref, _key: p._key, "
                    "phrase: p.phrase, english: p.english, conjugated: p.conjugated}",
                    bind_vars={"s": sample_ref},
                )
                existing_by_ref = {p["phrase_ref"]: p for p in existing_cursor}
            except Exception as exc:
                return Response({"error": f"Database error loading existing phrases: {exc}"}, status=500)

            to_insert = []
            to_update = []
            rollback_updates = []  # stores old values so rollback can restore them

            for p in phrases_to_create:
                ref = p["phrase_ref"]
                if ref in existing_by_ref:
                    old = existing_by_ref[ref]
                    rollback_updates.append({
                        "_key": old["_key"],
                        "phrase": old.get("phrase"),
                        "english": old.get("english"),
                        "conjugated": old.get("conjugated"),
                    })
                    to_update.append({"_key": old["_key"], "phrase": p["phrase"],
                                      "english": p["english"], "conjugated": p["conjugated"],
                                      "import_batch_id": batch_id})
                else:
                    p["import_batch_id"] = batch_id
                    to_insert.append(p)

            try:
                if to_insert:
                    db.collection("Phrases").insert_many(to_insert)
                for upd in to_update:
                    db.collection("Phrases").update(upd)
            except Exception as exc:
                return Response({"error": f"Failed to write phrases: {exc}"}, status=500)

            inserted_count = len(to_insert)
            updated_count = len(to_update)

        batch_record = {
            "batch_id": batch_id,
            "sample_ref": sample_ref,
            "phrase_count": inserted_count,
            "updated_count": updated_count,
            "skipped_empty_count": skipped_empty_count,
            "created_at": now,
            "created_by": request.user.username,
            "upgrade": upgrade,
            "rolled_back": False,
            "rollback_updates": rollback_updates,
        }
        try:
            db.collection("ImportBatches").insert(batch_record)
        except Exception:
            pass

        return Response({
            "batch_id": batch_id,
            "sample_ref": sample_ref,
            "phrase_count": inserted_count,
            "updated_count": updated_count,
            "skipped_count": skipped_empty_count,
            "created_at": now,
        }, status=201)

    @action(
        detail=False,
        methods=["delete"],
        url_path=r"import-batch/(?P<batch_id>[^/.]+)",
    )
    def rollback_import_batch(self, request, batch_id=None):
        """
        DELETE /samples/import-batch/{batch_id}/ — delete a sample and all its phrases
        created by a specific import batch. Admin only.
        """
        if not IsGlobalOrProjectAdmin().has_permission(request, self):
            return Response({"error": "Admin access required"}, status=403)

        db = request.arangodb

        # Load batch record to get stored old values for updated phrases
        try:
            batch_cursor = db.aql.execute(
                "FOR b IN ImportBatches FILTER b.batch_id == @bid RETURN b",
                bind_vars={"bid": batch_id},
            )
            batch_docs = list(batch_cursor)
        except Exception:
            batch_docs = []

        batch_doc = batch_docs[0] if batch_docs else {}

        if batch_doc.get("rolled_back"):
            return Response({"error": "This import has already been rolled back"}, status=400)

        # Delete newly inserted phrases (tagged with batch_id)
        phrases_cursor = db.aql.execute(
            "FOR p IN Phrases FILTER p.import_batch_id == @bid REMOVE p IN Phrases RETURN 1",
            bind_vars={"bid": batch_id},
        )
        deleted_phrases = len(list(phrases_cursor))

        # Restore previously updated phrases to their old values
        restored_count = 0
        for old in batch_doc.get("rollback_updates", []):
            try:
                db.collection("Phrases").update({
                    "_key": old["_key"],
                    "phrase": old.get("phrase"),
                    "english": old.get("english"),
                    "conjugated": old.get("conjugated"),
                })
                restored_count += 1
            except Exception:
                pass

        # Delete the sample document (only present in non-upgrade imports)
        sample_cursor = db.aql.execute(
            "FOR s IN Samples FILTER s.import_batch_id == @bid REMOVE s IN Samples RETURN s.sample_ref",
            bind_vars={"bid": batch_id},
        )
        deleted_samples = list(sample_cursor)

        if not batch_docs and not deleted_samples and deleted_phrases == 0:
            return Response({"error": "Import batch not found"}, status=404)

        try:
            db.aql.execute(
                "FOR b IN ImportBatches FILTER b.batch_id == @bid "
                "UPDATE b WITH {rolled_back: true, rolled_back_at: @ts} IN ImportBatches",
                bind_vars={"bid": batch_id, "ts": datetime.utcnow().isoformat()},
            )
        except Exception:
            pass

        return Response({
            "deleted_sample": deleted_samples[0] if deleted_samples else None,
            "deleted_phrases": deleted_phrases,
            "restored_phrases": restored_count,
        })

    @action(detail=False, methods=["get"], url_path="import-history")
    def import_history(self, request):
        """
        GET /samples/import-history/ — list all import batches, newest first.
        Admin only.
        """
        if not IsGlobalOrProjectAdmin().has_permission(request, self):
            return Response({"error": "Admin access required"}, status=403)

        db = request.arangodb
        try:
            cursor = db.aql.execute(
                "FOR b IN ImportBatches SORT b.created_at DESC RETURN b"
            )
            return Response(list(cursor))
        except Exception:
            return Response([])


class SourceViewSet(ArangoModelViewSet):
    """
    API endpoint for retrieving source metadata for samples.

    Sources contain information about the origin, recording conditions,
    and metadata for linguistic samples.

    Available endpoints:
    - GET /sources/ - List all sources
    - GET /sources/<id>/ - Retrieve specific source by ID

    Read-only access to source information including fieldworker details,
    recording quality, speaker information, and transcription status.
    """

    serializer_class = SourceSerializer
    model = Source
    http_method_names = ["get", "head", "options"]  # prevent post


class AnswerViewSet(ArangoModelViewSet):
    """
    API endpoint for retrieving answers to research questions.

    Query Parameters:
    - q: Question ID(s) - multiple values allowed
    - search: Field-based filters - "question_id,field,value" format only
    - s: Sample reference(s) - multiple values allowed
    - include_hidden: Set to "true" to include answers from non-visible samples (default: false)

    By default, only answers from visible samples are returned.

    Searchable Fields: form, marker

    Examples:
    - /answers/?q=1 - All answers for question 1
    - /answers/?search=1,form,verbal - Answers where form=verbal
    - /answers/?search=1,form,verbal&search=2,marker,past - Multiple field filters
    - /answers/?q=1&include_hidden=true - Include answers from hidden samples
    """

    serializer_class = AnswerSerializer
    model = Answer
    http_method_names = ["get", "post", "patch", "put", "delete", "head", "options"]
    permission_classes = [AllowAny]  # GET and query POST are public; write methods use per-action override

    # Structural fields that must never be overwritten via the API
    PROTECTED_FIELDS = {"_key", "_id", "_rev", "sample", "question_id", "category"}

    def get_permissions(self):
        if self.action in ("partial_update", "create_answer", "destroy") or self.request.method in ("PATCH", "PUT", "DELETE"):
            return [CanEditSample()]
        return [AllowAny()]

    def get_sample_ref(self, request):
        """Required by CanEditSample to resolve the target sample."""
        if self.action == "create_answer":
            return request.data.get("sample")
        pk = self.kwargs.get("pk")
        if not pk:
            return None
        db = request.arangodb
        doc = db.collection(self.model.collection_name).get(pk)
        return doc.get("sample") if doc else None

    def partial_update(self, request, pk=None):
        """
        PATCH /answers/{key}/ — update editable fields on an answer.
        Requires editor+ role. Editors with sample restrictions may only
        edit answers belonging to their allowed samples.

        Allowed fields: any non-structural field (see PROTECTED_FIELDS for what cannot be changed).
        """
        db = request.arangodb
        doc = db.collection(self.model.collection_name).get(pk)
        if not doc:
            raise NotFound(detail="Answer not found")

        updates = {k: v for k, v in request.data.items() if k not in self.PROTECTED_FIELDS}
        if not updates:
            return Response(
                {"error": "No updatable fields provided."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        db.collection(self.model.collection_name).update({"_key": pk, **updates}, keep_none=False)
        updated = db.collection(self.model.collection_name).get(pk)
        serializer = self.serializer_class(updated, context={"request": request})
        return Response(serializer.data)

    @action(detail=False, methods=["put"], url_path="create")
    def create_answer(self, request):
        """
        PUT /answers/create/ — create a new answer for a question+sample.
        Requires editor+ role.

        Body: { question_id, sample, field, value }
        """
        db = request.arangodb
        question_id = request.data.get("question_id")
        sample = request.data.get("sample")
        field = request.data.get("field")
        value = request.data.get("value", "")

        if not question_id or not sample or not field:
            return Response(
                {"error": "question_id, sample, and field are required"},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if field in self.PROTECTED_FIELDS:
            return Response(
                {"error": f"Field '{field}' cannot be set."},
                status=status.HTTP_400_BAD_REQUEST,
            )

        try:
            question_id = int(question_id)
        except (TypeError, ValueError):
            return Response({"error": "question_id must be an integer"}, status=status.HTTP_400_BAD_REQUEST)

        # Find the question document
        cursor = db.aql.execute(
            "FOR q IN ResearchQuestions FILTER q.id == @id RETURN q",
            bind_vars={"id": question_id},
        )
        questions = list(cursor)
        if not questions:
            raise NotFound(detail=f"Question {question_id} not found")
        question = questions[0]

        # Reject if an answer already exists for this question+sample
        existing_cursor = db.aql.execute(
            """
            FOR q IN ResearchQuestions FILTER q.id == @qid
              FOR a IN 1..1 OUTBOUND q GivesAnswer
                FILTER a.sample == @sample
                RETURN a._key
            """,
            bind_vars={"qid": question_id, "sample": sample},
        )
        if list(existing_cursor):
            return Response(
                {"error": "An answer already exists for this question and sample"},
                status=status.HTTP_409_CONFLICT,
            )

        # Insert the new answer document
        new_doc = {"sample": sample, "question_id": question_id, field: value}
        result = db.collection("Answers").insert(new_doc, return_new=True)
        answer_doc = result["new"]

        # Create the GivesAnswer edge from question → answer
        db.collection("GivesAnswer").insert({
            "_from": question["_id"],
            "_to": answer_doc["_id"],
        })

        return Response(answer_doc, status=status.HTTP_201_CREATED)

    def destroy(self, request, pk=None):
        """
        DELETE /answers/{key}/ — delete an answer document and its GivesAnswer edge.
        Requires editor+ role.
        """
        db = request.arangodb
        doc = db.collection(self.model.collection_name).get(pk)
        if not doc:
            raise NotFound(detail="Answer not found")

        # Remove the GivesAnswer edge(s) pointing to this answer
        db.aql.execute(
            "FOR e IN GivesAnswer FILTER e._to == @id REMOVE e IN GivesAnswer",
            bind_vars={"id": doc["_id"]},
        )

        db.collection(self.model.collection_name).delete(pk)
        return Response(status=status.HTTP_204_NO_CONTENT)

    def create(self, request):
        """
        POST handler for answers - accepts question IDs and sample refs in request body.
        Avoids URL length limitations when querying many question IDs.

        Request body (JSON):
        {
            "question_ids": [1, 2, 3, ...],
            "sample_refs": ["SAMPLE-001", ...],  // optional
            "include_hidden": false               // optional
        }
        """
        from rest_framework.response import Response

        try:
            body = request.data
            question_ids = body.get("question_ids", [])
            sample_refs = body.get("sample_refs", [])

            if not question_ids:
                raise NotFound(detail="At least one question ID is required")

            operator = body.get("operator", "OR").upper()
            if operator not in ("AND", "OR"):
                operator = "OR"
            answers = self.get_answers_for_questions(question_ids, sample_refs if sample_refs else None, operator)
            serializer = self.serializer_class(
                answers, many=True, context={"request": request, "view": self}
            )
            return Response(serializer.data)

        except (NotFound, ValidationError):
            raise
        except Exception as e:
            print(f"Error in POST answers: {e}")
            raise ValidationError(f"Error processing request: {str(e)}")

    def get_queryset(self):
        try:
            # Parse legacy q parameters
            question_ids = self.request.GET.getlist("q")

            # Parse new search parameters
            search_params = self.request.GET.getlist("search")
            search_filters = []

            for param in search_params:
                parts = param.split(',', 2)  # Limit to 3 parts to handle commas in values
                if len(parts) == 3:
                    # Question ID + field + value (required format)
                    search_filters.append({
                        "question_id": int(parts[0]),
                        "field": parts[1].strip(),
                        "value": parts[2].strip()
                    })
                else:
                    raise ValidationError(f"Invalid search parameter format: {param}. Use 'question_id,field,value' format")

            # Get sample filters
            sample_refs = self.request.GET.getlist("s")

            # AND/OR operator for combining multiple search criteria (default: OR)
            operator = self.request.GET.get("operator", "OR").upper()
            if operator not in ("AND", "OR"):
                operator = "OR"

            # Require at least one filter (legacy q or new search)
            if not question_ids and not search_filters:
                raise NotFound(
                    detail="At least one question ID (q parameter) or search filter is required"
                )

            # Use new search method if search parameters provided, otherwise legacy method
            if search_filters:
                return self.get_answers_with_field_filters(search_filters, sample_refs, operator)
            else:
                return self.get_answers_for_questions(question_ids, sample_refs, operator)
                
        except (NotFound, ValidationError):
            raise
        except ValueError as e:
            raise ValidationError(f"Invalid parameter value: {str(e)}")
        except Exception as e:
            print(f"Error fetching answers: {e}")
            return []

    def validate_questions(self, question_ids):
        """Validate all question IDs exist"""
        db = self.request.arangodb
        aql = "FOR q IN ResearchQuestions FILTER q.id IN @question_ids RETURN q.id"
        cursor = db.aql.execute(aql, bind_vars={"question_ids": question_ids})
        existing_questions = [qid for qid in cursor]
        missing_questions = set(question_ids) - set(existing_questions)
        if missing_questions:
            raise NotFound(detail=f"Questions not found: {sorted(missing_questions)}")

    def validate_samples(self, sample_refs):
        """Validate all sample references exist"""
        db = self.request.arangodb
        aql = "FOR s IN Samples FILTER s.sample_ref IN @sample_refs RETURN s.sample_ref"
        cursor = db.aql.execute(aql, bind_vars={"sample_refs": sample_refs})
        existing_samples = [ref for ref in cursor]
        missing_samples = set(sample_refs) - set(existing_samples)
        if missing_samples:
            raise NotFound(detail=f"Samples not found: {sorted(missing_samples)}")

    def get_visible_sample_refs(self):
        """Get list of visible sample references for filtering."""
        db = self.request.arangodb
        cursor = db.aql.execute('FOR s IN Samples FILTER s.visible == "Yes" RETURN s.sample_ref')
        return [ref for ref in cursor]

    def include_hidden(self):
        """Check if the request asks to include hidden (non-visible) samples."""
        # Global admin preference takes precedence
        if user_sees_hidden_samples(self.request.user):
            return True
        # Check GET params (for GET requests) and body (for POST requests)
        if self.request.GET.get("include_hidden", "").lower() in ("true", "1", "yes"):
            return True
        if self.request.method == "POST" and hasattr(self.request, 'data'):
            return bool(self.request.data.get("include_hidden", False))
        return False

    def get_answers_for_questions(self, question_ids, sample_refs=None, operator="OR"):
        """Helper method to get answers for multiple question IDs and sample references"""
        db = self.request.arangodb
        if not question_ids:
            raise NotFound(detail="At least one question ID is required")
        try:
            # Convert to integers
            question_ids = [int(qid) for qid in question_ids]

            # Validate all inputs upfront
            self.validate_questions(question_ids)
            if sample_refs:
                self.validate_samples(sample_refs)

            # Build single AQL query to get all answers for all questions
            filters = []
            bind_vars = {"question_ids": question_ids}

            if sample_refs:
                filters.append("FILTER answer.sample IN @samples")
                bind_vars["samples"] = sample_refs

            if not self.include_hidden():
                visible_refs = self.get_visible_sample_refs()
                filters.append("FILTER answer.sample IN @visible_samples")
                bind_vars["visible_samples"] = visible_refs

            filter_clause = "\n                ".join(filters)

            if operator == "AND" and len(question_ids) > 1:
                # Only return answers for samples that have answers to ALL selected questions
                aql = f"""
                LET all_answers = (
                  FOR question IN ResearchQuestions
                    FILTER question.id IN @question_ids
                    FOR answer IN 1..1 OUTBOUND question GivesAnswer
                      {filter_clause}
                      RETURN MERGE(answer, {{question_id: question.id}})
                )
                LET qualified_samples = (
                  FOR a IN all_answers
                    COLLECT sample = a.sample INTO grp
                    FILTER LENGTH(UNIQUE(grp[*].a.question_id)) == LENGTH(@question_ids)
                    RETURN sample
                )
                FOR a IN all_answers
                  FILTER a.sample IN qualified_samples
                  RETURN a
                """
            else:
                aql = f"""
                FOR question IN ResearchQuestions
                  FILTER question.id IN @question_ids
                  FOR answer IN 1..1 OUTBOUND question GivesAnswer
                    {filter_clause}
                    RETURN MERGE(answer, {{question_id: question.id}})
                """

            cursor = db.aql.execute(aql, bind_vars=bind_vars)
            answers = [doc for doc in cursor]
            answers.sort(key=lambda x: x.get("sample", ""))
            return answers
        except NotFound:
            raise
        except Exception as e:
            print(f"Error fetching answers: {e}")
            return []

    def get_answers_with_field_filters(self, search_filters, sample_refs=None, operator="OR"):
        """Get answers with field-based filtering using search parameters"""
        # Define allowed search fields for security
        # ALLOWED_SEARCH_FIELDS = {'form', 'marker', 'case_name'}
        
        db = self.request.arangodb
        if not search_filters:
            raise NotFound(detail="At least one search filter is required")
            
        try:
            # Validate field names and extract question IDs
            question_ids = []
            for filter_obj in search_filters:
                question_ids.append(filter_obj["question_id"])
                # if filter_obj["field"] and filter_obj["field"] not in ALLOWED_SEARCH_FIELDS:
                #     raise ValidationError(f"Field '{filter_obj['field']}' is not searchable. Allowed fields: {', '.join(sorted(ALLOWED_SEARCH_FIELDS))}")
            
            # Validate all question IDs exist
            question_ids = list(set(question_ids))  # Remove duplicates
            self.validate_questions(question_ids)
            
            # Validate sample references if provided
            if sample_refs:
                self.validate_samples(sample_refs)
            
            # Build dynamic AQL query with OR conditions for different questions
            conditions = []
            bind_vars = {}
            
            for i, filter_obj in enumerate(search_filters):
                qid = filter_obj["question_id"]
                field = filter_obj["field"]
                value = filter_obj["value"]
                
                # Match value allowing partial matches
                condition = f"(answer.question_id == @qid_{i} AND answer.{field} LIKE @value_{i})"
                bind_vars[f"qid_{i}"] = qid
                bind_vars[f"value_{i}"] = f"%{value}%"
                conditions.append(condition)
            
            # Combine all conditions with AND or OR
            joiner = f" {operator} "
            filter_clause = joiner.join(conditions)
            
            # Add sample filtering if provided
            extra_filters = ""
            if sample_refs:
                extra_filters += " AND answer.sample IN @sample_refs"
                bind_vars["sample_refs"] = sample_refs

            if not self.include_hidden():
                visible_refs = self.get_visible_sample_refs()
                extra_filters += " AND answer.sample IN @visible_samples"
                bind_vars["visible_samples"] = visible_refs

            # Build final AQL query
            aql = f"""
            FOR answer IN Answers
              FILTER ({filter_clause}){extra_filters}
              RETURN answer
            """
            
            cursor = db.aql.execute(aql, bind_vars=bind_vars)
            answers = [doc for doc in cursor]
            # sort by sample reference
            answers.sort(key=lambda x: x.get("sample", ""))
            return answers
            
        except (NotFound, ValidationError):
            raise
        except Exception as e:
            print(f"Error fetching answers with field filters: {e}")
            return []


class ViewViewSet(ArangoModelViewSet):
    """
    API endpoint for retrieving HTML template views.

    Views contain HTML templates with associated filenames and parent categories.

    Available endpoints:
    - GET /views/ - List all views
    - GET /views/?filename=<filename> - Retrieve a specific view by filename
    - GET /views/<_key>/ - Retrieve specific view by _key
    """

    model = View
    serializer_class = ViewSerializer
    http_method_names = ["get", "head", "options"]  # Read-only access

    def list(self, request):
        filename = request.query_params.get("filename")
        if filename:
            doc = View.get_by_field("filename", filename)
            if not doc:
                raise NotFound(detail=f"View not found for filename: {filename}")
            serializer = self.serializer_class(
                doc, context={"request": request, "view": self}
            )
            return Response([serializer.data])
        return super().list(request)


class TranscriptionViewSet(ArangoModelViewSet):
    """
    API endpoint for retrieving transcriptions for specific samples.

    Transcriptions are text records associated with audio samples, organized by segments.
    A sample parameter is required to retrieve transcriptions.

    Available endpoints:
    - GET /transcriptions/?sample=<sample_ref> - List transcriptions for a specific sample (REQUIRED)
    - GET /transcriptions/<id>/ - Retrieve specific transcription by ID
    - POST /transcriptions/search/ - Search transcriptions across multiple samples
    - POST /transcriptions/export/ - Export matching transcriptions (no pagination)
    - GET /transcriptions/by-answer/?answer_key=<key> - Get transcriptions linked to an answer

    Query Parameters:
    - sample (required): Sample reference (e.g., sample=AL-001)
    - answer_key (required for by-answer): Answer _key

    Examples:
    - /transcriptions/?sample=AL-001 - Transcriptions for sample AL-001
    - /transcriptions/123/ - Specific transcription with ID 123
    - /transcriptions/by-answer/?answer_key=ABC123 - Transcriptions linked to answer ABC123

    Results are sorted by segment_no in ascending order.
    """

    model = Transcription
    serializer_class = TranscriptionSerializer
    http_method_names = ["get", "post", "patch", "head", "options"]
    permission_classes = [AllowAny]  # GET and search POST are public; PATCH uses per-action override

    EDITABLE_FIELDS = {"transcription", "english", "gloss", "segment_no"}

    def get_permissions(self):
        if self.request.method == "PATCH":
            return [CanEditSample()]
        return [AllowAny()]

    def get_sample_ref(self, request):
        """Required by CanEditSample to resolve the target sample."""
        pk = self.kwargs.get("pk")
        if not pk:
            return None
        db = request.arangodb
        doc = db.collection(self.model.collection_name).get(pk)
        return doc.get("sample") if doc else None

    def partial_update(self, request, pk=None):
        """
        PATCH /transcriptions/{key}/ — update editable fields on a transcription.
        Requires editor+ role. Editors with sample restrictions may only
        edit transcriptions belonging to their allowed samples.

        Allowed fields: transcription, english, gloss, segment_no
        """
        db = request.arangodb
        doc = db.collection(self.model.collection_name).get(pk)
        if not doc:
            raise NotFound(detail="Transcription not found")

        updates = {k: v for k, v in request.data.items() if k in self.EDITABLE_FIELDS}
        if not updates:
            return Response(
                {"error": f"No editable fields provided. Allowed: {sorted(self.EDITABLE_FIELDS)}"},
                status=status.HTTP_400_BAD_REQUEST,
            )

        db.collection(self.model.collection_name).update({"_key": pk, **updates})
        updated = db.collection(self.model.collection_name).get(pk)
        serializer = self.serializer_class(updated, context={"request": request})
        return Response(serializer.data)

    def get_queryset(self):
        try:
            sample = self.request.query_params.get("sample")
            if not sample:
                raise NotFound(
                    detail="Sample parameter is required to fetch transcriptions"
                )

            db = self.request.arangodb
            # collection = db.collection(self.model.collection_name)

            # Use AQL to sort by segment_no
            aql_query = """
            FOR transcription IN Transcriptions
            FILTER transcription.sample == @sample
            SORT transcription.segment_no ASC
            RETURN transcription
            """

            cursor = db.aql.execute(aql_query, bind_vars={"sample": sample})
            return [transcription for transcription in cursor]

        except NotFound:
            raise
        except Exception as e:
            print(f"Error fetching transcriptions: {e}")
            return []

    @action(detail=False, methods=["get"], url_path="by-answer")
    def by_answer(self, request):
        """
        Get transcriptions associated with an answer via its research question.

        Query Parameters:
        - answer_key (required): Answer _key

        Returns transcriptions where transcription.sample == answer.sample
        AND the transcription links to the answer's question (directly via
        question_ids, or via a covering category whose subtree includes
        this question — category_ids intersects the question's
        hierarchy_ids). See extract/master_phrases_migration/PLAN.md —
        this replaces the old HasTag edge traversal.

        If the answer has `transcription_overrides.include` set
        (non-empty), that list of Transcription _keys is used instead of
        the question/category match entirely — preserves per-answer
        precision for the ~65 heterogeneous questions (see PhraseViewSet.
        by_answer's docstring for the full rationale).
        `transcription_overrides.exclude` is always subtracted.
        """
        from rest_framework.exceptions import ValidationError

        try:
            answer_key = request.query_params.get("answer_key")
            if not answer_key:
                raise ValidationError("Answer key parameter is required")

            db = request.arangodb
            answer = db.collection("Answers").get(answer_key)
            if not answer:
                raise NotFound(detail="Answer not found")

            sample = answer.get('sample')
            if not sample:
                return Response([])

            overrides = answer.get('transcription_overrides') or {}
            include = overrides.get('include') or []
            exclude = list(overrides.get('exclude') or [])
            transcriptions = self._resolve_transcriptions(db, sample, answer.get('question_id'), include, exclude)

            if not transcriptions:
                return Response([])

            # Sort by segment_no
            transcriptions.sort(key=lambda x: x.get("segment_no", 0))
            serializer = self.serializer_class(transcriptions, many=True, context={"request": request})
            return Response(serializer.data)

        except (NotFound, ValidationError):
            raise
        except Exception as e:
            print(f"Error fetching transcriptions for answer: {e}")
            raise NotFound(detail="Error retrieving transcriptions")

    def _resolve_transcriptions(self, db, sample, question_id, include, exclude):
        """Resolves the transcription list for a question_id: via
        Answer.transcription_overrides.include (exact Transcription _keys)
        if set, otherwise via _transcriptions_by_category. Shared by
        by_answer and RelatedContentViewSet."""
        if include:
            aql = """
                FOR key IN @include
                    FILTER key NOT IN @exclude
                    LET t = DOCUMENT(CONCAT("Transcriptions/", key))
                    FILTER t != null AND t.sample == @sample
                    RETURN t
            """
            return list(db.aql.execute(aql, bind_vars={
                'include': include,
                'exclude': exclude,
                'sample': sample,
            }))
        return self._transcriptions_by_category(db, question_id, sample, exclude=exclude)

    def _transcriptions_by_category(self, db, category_id, sample, exclude=None):
        """Transcriptions linked to a research question/category id, scoped
        to one sample. Transcriptions have no MasterPhrase-style split (each
        one already stands alone), so unlike phrases there's no
        question_overrides layer here — just the direct question_ids/
        category_ids match. Shared by by_answer's question/category branch
        and by_category."""
        exclude = exclude or []
        hierarchy_ids = _get_question_hierarchy_ids(db, category_id)
        if hierarchy_ids is None:
            return []

        aql = """
            FOR transcription IN Transcriptions
                FILTER transcription.sample == @sample
                FILTER (@category_id IN (transcription.question_ids || [])
                        OR LENGTH(INTERSECTION(transcription.category_ids || [], @hierarchy_ids)) > 0)
                    AND transcription._key NOT IN @exclude
                RETURN transcription
        """
        return list(db.aql.execute(aql, bind_vars={
            'sample': sample, 'category_id': category_id, 'hierarchy_ids': hierarchy_ids, 'exclude': exclude,
        }))

    @action(detail=False, methods=["get"], url_path="by-category")
    def by_category(self, request):
        """
        GET /transcriptions/by-category/?category_id=<id>&sample=<sample_ref>

        Same matching as by-answer's question/category branch, but keyed
        directly by a stable ResearchQuestion/Category id + sample instead
        of an Answer _key — see PhraseViewSet.by_category's docstring for
        the full rationale (category/question ids are stable across
        re-imports; Answer._key is not).

        Does not apply Answer.transcription_overrides (there's no specific
        Answer in play here) — only the direct question_ids/category_ids match.
        """
        from rest_framework.exceptions import ValidationError

        try:
            category_id = request.query_params.get("category_id")
            sample = request.query_params.get("sample")
            if not category_id:
                raise ValidationError("category_id parameter is required")
            if not sample:
                raise ValidationError("sample parameter is required")
            try:
                category_id = int(category_id)
            except (TypeError, ValueError):
                raise ValidationError("category_id must be an integer")

            db = request.arangodb
            transcriptions = self._transcriptions_by_category(db, category_id, sample)

            if not transcriptions:
                return Response([])

            transcriptions.sort(key=lambda x: x.get("segment_no", 0))
            serializer = self.serializer_class(transcriptions, many=True, context={"request": request})
            return Response(serializer.data)

        except (NotFound, ValidationError):
            raise
        except Exception as e:
            print(f"Error fetching transcriptions for category: {e}")
            raise NotFound(detail="Error retrieving transcriptions")

    def _get_visible_sample_refs(self, request):
        """Get sample refs respecting the show_hidden_samples preference."""
        db = request.arangodb
        if user_sees_hidden_samples(request.user):
            cursor = db.aql.execute("FOR s IN Samples RETURN s.sample_ref")
        else:
            cursor = db.aql.execute("FOR s IN Samples FILTER s.visible == 'Yes' RETURN s.sample_ref")
        return list(cursor)

    @action(detail=False, methods=["post"], url_path="search")
    def search(self, request):
        """
        Search transcriptions across multiple samples.

        Request Body:
        - query (required, min 2 chars): Search term
        - sample_refs (optional): List of sample refs to limit search
        - sort (optional, default 'segment_no'): Sort field — 'segment_no' or 'sample'
        - page (optional, default 1): Page number
        - page_size (optional, default 50, max 200): Results per page
        - field (optional, default 'both'): 'romani', 'english', or 'both'
        """
        query = request.data.get("query", "").strip()
        if not query or len(query) < 2:
            raise ValidationError("Query must be at least 2 characters")

        sample_refs = request.data.get("sample_refs", [])
        sort = request.data.get("sort", "segment_no")
        field = request.data.get("field", "both")
        page = int(request.data.get("page", 1))
        page_size = min(int(request.data.get("page_size", 50)), 200)
        offset = (page - 1) * page_size

        db = request.arangodb
        query_lower = query.lower()

        sort_clauses = {
            "segment_no": "SORT t.sample, t.segment_no",
            "sample": "SORT t.sample, t.segment_no",
        }
        sort_aql = sort_clauses.get(sort, sort_clauses["segment_no"])

        if not sample_refs:
            sample_refs = self._get_visible_sample_refs(request)

        if field == "romani":
            like_expr = 'LIKE(t.transcription, CONCAT("%", @query, "%"))'
        elif field == "english":
            like_expr = 'LIKE(t.english, CONCAT("%", @query, "%"))'
        else:
            like_expr = 'LIKE(t.transcription, CONCAT("%", @query, "%")) OR LIKE(t.english, CONCAT("%", @query, "%"))'
        search_filter = f'SEARCH ANALYZER({like_expr}, "norm_lower")'

        count_aql = f"""
            FOR t IN TranscriptionSearch
                {search_filter}
                FILTER t.sample IN @sample_refs
                COLLECT WITH COUNT INTO total
                RETURN total
        """

        results_aql = f"""
            LET sample_lookup = (
                FOR s IN Samples
                    RETURN {{ref: s.sample_ref, label: CONCAT_SEPARATOR(', ', s.dialect_name, s.location)}}
            )
            LET sample_map = ZIP(sample_lookup[*].ref, sample_lookup[*].label)
            FOR t IN TranscriptionSearch
                {search_filter}
                FILTER t.sample IN @sample_refs
                {sort_aql}
                LIMIT @offset, @page_size
                RETURN MERGE(t, {{
                    sample_label: sample_map[t.sample]
                }})
        """

        count_bind = {"query": query_lower, "sample_refs": sample_refs}
        results_bind = {"query": query_lower, "sample_refs": sample_refs, "offset": offset, "page_size": page_size}

        try:
            count_cursor = db.aql.execute(count_aql, bind_vars=count_bind)
            total = next(count_cursor, 0)

            results_cursor = db.aql.execute(results_aql, bind_vars=results_bind)
            results = list(results_cursor)

            serializer = self.serializer_class(
                results, many=True, context={"request": request}
            )

            return Response({
                "count": total,
                "page": page,
                "page_size": page_size,
                "results": serializer.data,
            })
        except Exception as e:
            print(f"Error searching transcriptions: {e}")
            raise ValidationError(f"Search failed: {str(e)}")

    @action(detail=False, methods=["post"], url_path="export")
    def export(self, request):
        """
        Export all matching transcriptions (no pagination) for download.
        Same parameters as search but returns all results.
        """
        query = request.data.get("query", "").strip()
        if not query or len(query) < 2:
            raise ValidationError("Query must be at least 2 characters")

        sample_refs = request.data.get("sample_refs", [])
        sort = request.data.get("sort", "segment_no")
        field = request.data.get("field", "both")

        db = request.arangodb
        query_lower = query.lower()

        sort_clauses = {
            "segment_no": "SORT t.sample, t.segment_no",
            "sample": "SORT t.sample, t.segment_no",
        }
        sort_aql = sort_clauses.get(sort, sort_clauses["segment_no"])

        if not sample_refs:
            sample_refs = self._get_visible_sample_refs(request)

        if field == "romani":
            like_expr = 'LIKE(t.transcription, CONCAT("%", @query, "%"))'
        elif field == "english":
            like_expr = 'LIKE(t.english, CONCAT("%", @query, "%"))'
        else:
            like_expr = 'LIKE(t.transcription, CONCAT("%", @query, "%")) OR LIKE(t.english, CONCAT("%", @query, "%"))'
        search_filter = f'SEARCH ANALYZER({like_expr}, "norm_lower")'

        export_aql = f"""
            LET sample_lookup = (
                FOR s IN Samples
                    RETURN {{ref: s.sample_ref, label: CONCAT_SEPARATOR(', ', s.dialect_name, s.location)}}
            )
            LET sample_map = ZIP(sample_lookup[*].ref, sample_lookup[*].label)
            FOR t IN TranscriptionSearch
                {search_filter}
                FILTER t.sample IN @sample_refs
                {sort_aql}
                RETURN {{
                    sample: t.sample,
                    sample_label: sample_map[t.sample],
                    segment_no: t.segment_no,
                    transcription: t.transcription,
                    english: t.english,
                    gloss: t.gloss
                }}
        """

        try:
            cursor = db.aql.execute(export_aql, bind_vars={"query": query_lower, "sample_refs": sample_refs})
            return Response(list(cursor))
        except Exception as e:
            print(f"Error exporting transcriptions: {e}")
            raise ValidationError(f"Export failed: {str(e)}")


class RelatedContentViewSet(ViewSet):
    """
    API endpoint for the "click a table cell" hot path: fetches both
    phrases and transcriptions related to a research question/category id
    in one request, instead of the client firing separate calls to
    PhraseViewSet.by_category and TranscriptionViewSet.by_category.

    GET /related/?category_id=<id>&sample=<sample_ref>&answer_key=<optional>

    If answer_key is given, that Answer is fetched once and reused to
    decide — independently for phrases and transcriptions — whether to
    honor its phrase_overrides/transcription_overrides (the ~65 "divergent
    questions" case, see PhraseViewSet.by_answer's docstring) instead of
    the plain category/sample match. This also avoids the double Answer
    lookup that calling by-answer for both phrases and transcriptions
    would otherwise cost.

    Response: { "phrases": [...], "transcriptions": [...] }
    """

    permission_classes = [AllowAny]

    def list(self, request):
        category_id = request.query_params.get("category_id")
        sample = request.query_params.get("sample")
        answer_key = request.query_params.get("answer_key")
        if not category_id:
            raise ValidationError("category_id parameter is required")
        if not sample:
            raise ValidationError("sample parameter is required")
        try:
            category_id = int(category_id)
        except (TypeError, ValueError):
            raise ValidationError("category_id must be an integer")

        db = request.arangodb
        answer = db.collection("Answers").get(answer_key) if answer_key else None

        phrase_overrides = (answer or {}).get("phrase_overrides") or {}
        phrase_view = PhraseViewSet()
        phrases = phrase_view._resolve_phrases(
            db, sample, category_id,
            phrase_overrides.get("include") or [],
            phrase_overrides.get("exclude") or [],
        )
        phrases = natsorted(phrases, key=lambda x: x.get("phrase_ref", ""))
        phrase_data = PhraseSerializer(phrases, many=True, context={"request": request}).data

        transcription_overrides = (answer or {}).get("transcription_overrides") or {}
        transcription_view = TranscriptionViewSet()
        transcriptions = transcription_view._resolve_transcriptions(
            db, sample, category_id,
            transcription_overrides.get("include") or [],
            transcription_overrides.get("exclude") or [],
        )
        transcriptions.sort(key=lambda x: x.get("segment_no", 0))
        transcription_data = TranscriptionSerializer(transcriptions, many=True, context={"request": request}).data

        return Response({"phrases": phrase_data, "transcriptions": transcription_data})


class BackupViewSet(ViewSet):
    """
    API endpoint for ArangoDB backup management using arangodump/arangorestore.

    GET    /backups/              — list all backups
    POST   /backups/              — create a backup  {"label": "optional"}
    DELETE /backups/{id}/         — delete a backup
    POST   /backups/{id}/restore/ — restore a backup
    """
    permission_classes = [IsGlobalOrProjectAdmin]
    lookup_value_regex = r"[^/]+"

    BACKUP_DIR = os.environ.get("BACKUP_DIR", os.path.join(settings.BASE_DIR, "backups"))

    def _arango_args(self):
        """Common arangodump/arangorestore connection arguments."""
        return [
            "--server.endpoint", settings.ARANGO_HOST.replace("http://", "tcp://").replace("https://", "ssl://"),
            "--server.username", settings.ARANGO_USERNAME.strip(),
            "--server.password", settings.ARANGO_PASSWORD,
            "--server.database", settings.ARANGO_DB_NAME,
        ]

    def _read_meta(self, backup_path):
        """Read metadata for a single backup directory."""
        meta_path = os.path.join(backup_path, "meta.json")
        if os.path.exists(meta_path):
            with open(meta_path) as f:
                return json.loads(f.read())
        return None

    def list(self, request):
        os.makedirs(self.BACKUP_DIR, exist_ok=True)
        backups = []
        for name in os.listdir(self.BACKUP_DIR):
            path = os.path.join(self.BACKUP_DIR, name)
            if not os.path.isdir(path):
                continue
            meta = self._read_meta(path)
            if meta:
                backups.append(meta)
            else:
                backups.append({
                    "id": name,
                    "datetime": datetime.fromtimestamp(os.path.getctime(path)).isoformat(),
                })
        backups.sort(key=lambda b: b.get("datetime", ""), reverse=True)
        return Response(backups)

    def create(self, request):
        import subprocess
        label = request.data.get("label", "manual")
        now = datetime.now()
        backup_id = f"{now.strftime('%Y-%m-%dT%H.%M.%S')}_{label}"
        backup_path = os.path.join(self.BACKUP_DIR, backup_id)
        os.makedirs(backup_path, exist_ok=True)

        cmd = ["arangodump", "--output-directory", backup_path, "--overwrite", "true"] + self._arango_args()
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        if result.returncode != 0:
            shutil.rmtree(backup_path, ignore_errors=True)
            return Response(
                {"error": f"arangodump failed: {result.stderr}"},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )

        meta = {"id": backup_id, "datetime": now.isoformat(), "label": label}
        with open(os.path.join(backup_path, "meta.json"), "w") as f:
            f.write(json.dumps(meta))

        return Response(meta, status=status.HTTP_201_CREATED)

    def destroy(self, request, pk=None):
        backup_path = os.path.join(self.BACKUP_DIR, pk)
        if not os.path.isdir(backup_path):
            raise NotFound(detail="Backup not found")
        shutil.rmtree(backup_path)
        return Response(status=status.HTTP_204_NO_CONTENT)

    @action(detail=True, methods=["post"])
    def restore(self, request, pk=None):
        import subprocess
        backup_path = os.path.join(self.BACKUP_DIR, pk)
        if not os.path.isdir(backup_path):
            raise NotFound(detail="Backup not found")

        cmd = ["arangorestore", "--input-directory", backup_path, "--overwrite", "true"] + self._arango_args()
        result = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
        if result.returncode != 0:
            return Response(
                {"error": f"arangorestore failed: {result.stderr}"},
                status=status.HTTP_500_INTERNAL_SERVER_ERROR,
            )
        return Response({"restored": pk})

