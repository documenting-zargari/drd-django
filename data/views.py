import json
import os
import shutil
from datetime import datetime

from django.conf import settings


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

from data.models import Answer, Category, Phrase, Sample, Source, Transcription, View
from data.serializers import (
    AnswerSerializer,
    CategorySerializer,
    PhraseSerializer,
    SampleSerializer,
    SourceSerializer,
    TranscriptionSerializer,
    ViewSerializer,
)
from roma.views import ArangoModelViewSet
from user.permissions import CanEditSample, IsGlobalOrProjectAdmin, IsProjectEditor


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

class PhraseViewSet(ArangoModelViewSet):
    """
    API endpoint for retrieving phrases associated with samples.

    Phrases are linguistic data linked to specific samples.

    Available endpoints:
    - GET /phrases/?sample=<sample_ref> - List phrases for a specific sample
    - GET /phrases/list/ - Unique phrase list for the phrase picker (phrase_ref + english only)
    - GET /phrases/<id>/ - Retrieve specific phrase by ID
    - GET /phrases/by-answer/?answer_key=<key> - Get phrases linked to an answer via phrase tags
    - POST /phrases/search/ - Search phrases (see that endpoint for parameters)
    - POST /phrases/export/ - Export matching phrases without pagination (see that endpoint for parameters)

    Query Parameters:
    - sample (required for list): Sample reference (e.g., sample=AL-001)
    - answer_key (required for by-answer): Answer _key

    Examples:
    - /phrases/?sample=AL-001 - Phrases for sample AL-001
    - /phrases/list/ - Unique phrase list for picker (phrase_ref + english only)
    - /phrases/123/ - Specific phrase with ID 123
    - /phrases/by-answer/?answer_key=ABC123 - Phrases linked to answer ABC123 via phrase tags
    """

    model = Phrase
    serializer_class = PhraseSerializer
    http_method_names = ["get", "post", "patch", "head", "options"]
    permission_classes = [AllowAny]  # GET and search POST are public; PATCH uses per-action override

    EDITABLE_FIELDS = {"phrase", "english", "conjugated", "tag_ids"}

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
        PATCH /phrases/{key}/ — update editable fields on a phrase.
        Requires editor+ role. Editors with sample restrictions may only
        edit phrases belonging to their allowed samples.

        Allowed fields: phrase, english, conjugated
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

        db.collection(self.model.collection_name).update({"_key": pk, **updates})
        updated = db.collection(self.model.collection_name).get(pk)
        serializer = self.serializer_class(updated, context={"request": request})
        return Response(serializer.data)

    def get_queryset(self):
        try:
            sample = self.request.query_params.get("sample")
            if not sample:
                raise NotFound(detail="Sample parameter is required to fetch phrases")

            db = self.request.arangodb
            collection = db.collection(self.model.collection_name)
            cursor = collection.find({"sample": sample})
            return [phrase for phrase in natsorted(cursor, key=lambda x: x["phrase_ref"])]

        except NotFound:
            raise
        except Exception as e:
            print(f"Error fetching phrases: {e}")
            return []

    @action(detail=False, methods=["get"], url_path="list")
    def phrase_list(self, request):
        """
        GET /phrases/list/ — unique phrases for the phrase picker.
        Returns one record per phrase_ref (deduplicated across samples),
        with only phrase_ref and english fields, sorted naturally by phrase_ref.
        """
        db = request.arangodb
        aql = """
            FOR p IN Phrases
                COLLECT ref = p.phrase_ref INTO g
                LET doc = FIRST(g[*].p)
                SORT ref
                RETURN { phrase_ref: doc.phrase_ref, english: doc.english }
        """
        cursor = db.aql.execute(aql)
        results = natsorted(list(cursor), key=lambda x: x["phrase_ref"])
        return Response(results)


    @action(detail=False, methods=["get"], url_path="tags")
    def tags(self, request):
        """
        GET /phrases/tags/ — all phrase tags for client-side hierarchy display.
        Returns: [{ "id": 1, "tag": "go", "parent_id": 5 }, ...]
        """
        db = request.arangodb
        aql = """
            FOR tag IN PhraseTags
                RETURN { id: tag.id, tag: tag.tag, parent_id: tag.parent_id }
        """
        cursor = db.aql.execute(aql)
        return Response(list(cursor))

    @action(detail=False, methods=["get"], url_path="by-answer")
    def by_answer(self, request):
        """
        Get phrases associated with an answer via sample + tag matching.

        Query Parameters:
        - answer_key (required): Answer _key

        Returns phrases where:
        - phrase.sample == answer.sample AND answer.tag_id IN phrase.tag_ids
        - OR if tag_id is NULL but tag_word exists, searches phrase text and english
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

            # Collect tag_ids and tag_words from answer
            tag_ids = []
            tag_words = []

            # Handle single tag format
            if 'tag' in answer and answer['tag']:
                tag = answer['tag']
                if tag.get('tag_id'):
                    tag_ids.append(tag['tag_id'])
                elif tag.get('name'):
                    tag_words.append(tag['name'])

            # Handle tags array format
            if 'tags' in answer and answer['tags']:
                for tag in answer['tags']:
                    if tag.get('tag_id'):
                        tag_ids.append(tag['tag_id'])
                    elif tag.get('tag_word'):
                        tag_words.append(tag['tag_word'])

            phrases = []

            # Query by tag_ids if available
            if tag_ids:
                aql = """
                    FOR phrase IN Phrases
                        FILTER phrase.sample == @sample
                        FILTER LENGTH(INTERSECTION(phrase.tag_ids || [], @tag_ids)) > 0
                        RETURN phrase
                """
                phrases.extend(db.aql.execute(aql, bind_vars={
                    'sample': sample,
                    'tag_ids': tag_ids
                }))

            # Fallback: text search by tag_words
            # First try exact match on english field (for tags like "be (PRES)")
            # Then fall back to regex word matching on english field
            # Strip bracketed annotations like [<CONJ>], [PAST] before searching
            if tag_words and not phrases:
                import re
                for tag_word in tag_words:
                    # Strip bracketed annotations (e.g., "did[<CONJ>]" -> "did", "put [PAST]" -> "put")
                    clean_tag = re.sub(r'\s*\[.*?\]', '', tag_word).strip()
                    if not clean_tag:
                        clean_tag = tag_word

                    # Try exact case-insensitive match on english field first
                    aql_exact = """
                        FOR phrase IN Phrases
                            FILTER phrase.sample == @sample
                            FILTER LOWER(phrase.english || '') == LOWER(@tag_word)
                            RETURN phrase
                    """
                    phrases.extend(db.aql.execute(aql_exact, bind_vars={
                        'sample': sample,
                        'tag_word': clean_tag
                    }))

                    # If no exact match, try regex word match on english field
                    if not phrases:
                        escaped_tag = re.escape(clean_tag)
                        aql_regex = """
                            FOR phrase IN Phrases
                                FILTER phrase.sample == @sample
                                FILTER REGEX_TEST(phrase.english || '', CONCAT('(?i)(^|[^a-zA-Z])', @escaped_tag, '([^a-zA-Z]|$)'))
                                RETURN phrase
                        """
                        phrases.extend(db.aql.execute(aql_regex, bind_vars={
                            'sample': sample,
                            'escaped_tag': escaped_tag
                        }))

            if not phrases:
                return Response([])

            # Deduplicate and sort
            seen = set()
            unique_phrases = []
            for p in phrases:
                if p['_key'] not in seen:
                    seen.add(p['_key'])
                    unique_phrases.append(p)

            phrases = natsorted(unique_phrases, key=lambda x: x.get("phrase_ref", ""))
            serializer = self.serializer_class(phrases, many=True, context={"request": request})
            return Response(serializer.data)

        except (NotFound, ValidationError):
            raise
        except Exception as e:
            print(f"Error fetching phrases for answer: {e}")
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
            sample_filter = "FILTER phrase.sample IN @sample_refs" if sample_refs else ""
            bind: dict = {"phrase_ref": phrase_ref}
            if sample_refs:
                bind["sample_refs"] = sample_refs
            results_aql = f"""
                FOR phrase IN Phrases
                    FILTER phrase.phrase_ref == @phrase_ref
                    {sample_filter}
                    FOR s IN Samples
                        FILTER s.sample_ref == phrase.sample
                        {visibility_filter}
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

            # Uses PhraseSearch ArangoSearch view with norm_lower analyzer for fast
            # case-insensitive substring matching via LIKE with wildcards
            query_lower = query.lower()
            if field == "romani":
                like_expr = 'LIKE(phrase.phrase, CONCAT("%", @query, "%"))'
            elif field == "english":
                like_expr = 'LIKE(phrase.english, CONCAT("%", @query, "%"))'
            else:
                like_expr = 'LIKE(phrase.phrase, CONCAT("%", @query, "%")) OR LIKE(phrase.english, CONCAT("%", @query, "%"))'
            search_filter = f'SEARCH ANALYZER({like_expr}, "norm_lower")'

            count_aql = f"""
                FOR phrase IN PhraseSearch
                    {search_filter}
                    FILTER phrase.sample IN @sample_refs
                    COLLECT WITH COUNT INTO total
                    RETURN total
            """
            results_aql = f"""
                LET sample_lookup = (
                    FOR s IN Samples
                        RETURN {{ref: s.sample_ref, label: CONCAT_SEPARATOR(', ', s.dialect_name, s.location)}}
                )
                LET sample_map = ZIP(sample_lookup[*].ref, sample_lookup[*].label)
                FOR phrase IN PhraseSearch
                    {search_filter}
                    FILTER phrase.sample IN @sample_refs
                    {sort_aql}
                    LIMIT @offset, @page_size
                    RETURN MERGE(phrase, {{
                        sample_label: sample_map[phrase.sample]
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
            sample_filter = "FILTER phrase.sample IN @sample_refs" if sample_refs else ""
            bind: dict = {"phrase_ref": phrase_ref}
            if sample_refs:
                bind["sample_refs"] = sample_refs
            export_aql = f"""
                FOR phrase IN Phrases
                    FILTER phrase.phrase_ref == @phrase_ref
                    {sample_filter}
                    FOR s IN Samples
                        FILTER s.sample_ref == phrase.sample
                        {visibility_filter}
                        LET sample_label = CONCAT_SEPARATOR(', ', s.dialect_name, s.location)
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
            if field == "romani":
                like_expr = 'LIKE(phrase.phrase, CONCAT("%", @query, "%"))'
            elif field == "english":
                like_expr = 'LIKE(phrase.english, CONCAT("%", @query, "%"))'
            else:
                like_expr = 'LIKE(phrase.phrase, CONCAT("%", @query, "%")) OR LIKE(phrase.english, CONCAT("%", @query, "%"))'
            search_filter = f'SEARCH ANALYZER({like_expr}, "norm_lower")'
            bind = {"query": query_lower, "sample_refs": sample_refs}
            export_aql = f"""
                LET sample_lookup = (
                    FOR s IN Samples
                        RETURN {{ref: s.sample_ref, label: CONCAT_SEPARATOR(', ', s.dialect_name, s.location)}}
                )
                LET sample_map = ZIP(sample_lookup[*].ref, sample_lookup[*].label)
                FOR phrase IN PhraseSearch
                    {search_filter}
                    FILTER phrase.sample IN @sample_refs
                    {sort_aql}
                    LET sample_label = sample_map[phrase.sample]
                    {export_fields}
            """

        try:
            cursor = db.aql.execute(export_aql, bind_vars=bind)
            return Response(list(cursor))
        except Exception as e:
            print(f"Error exporting phrases: {e}")
            raise ValidationError(f"Export failed: {str(e)}")


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
    http_method_names = ["get", "patch", "head", "options"]

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
    PROTECTED_FIELDS = {"_key", "_id", "_rev", "sample", "question_id", "category", "tag", "tags", "tag_id", "tag_ids"}

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

            answers = self.get_answers_for_questions(question_ids, sample_refs if sample_refs else None)
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

            # Require at least one filter (legacy q or new search)
            if not question_ids and not search_filters:
                raise NotFound(
                    detail="At least one question ID (q parameter) or search filter is required"
                )

            # Use new search method if search parameters provided, otherwise legacy method
            if search_filters:
                return self.get_answers_with_field_filters(search_filters, sample_refs)
            else:
                return self.get_answers_for_questions(question_ids, sample_refs)
                
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

    def get_answers_for_questions(self, question_ids, sample_refs=None):
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

    def get_answers_with_field_filters(self, search_filters, sample_refs=None):
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
            
            # Combine all conditions with OR
            filter_clause = " OR ".join(conditions)
            
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
        Get transcriptions associated with an answer via sample + tag matching.

        Query Parameters:
        - answer_key (required): Answer _key

        Returns transcriptions where:
        - transcription.sample == answer.sample AND transcription has HasTag edge to matching tag_id
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

            # Collect tag_ids from answer
            tag_ids = []

            # Handle single tag format
            if 'tag' in answer and answer['tag']:
                tag = answer['tag']
                if tag.get('tag_id'):
                    tag_ids.append(tag['tag_id'])

            # Handle tags array format
            if 'tags' in answer and answer['tags']:
                for tag in answer['tags']:
                    if tag.get('tag_id'):
                        tag_ids.append(tag['tag_id'])

            transcriptions = []

            if tag_ids:
                # Find transcriptions via HasTag edges to PhraseTags
                aql = """
                    FOR transcription IN Transcriptions
                        FILTER transcription.sample == @sample
                        LET tags = (
                            FOR tag IN 1..1 OUTBOUND transcription HasTag
                                FILTER tag.id IN @tag_ids
                                RETURN tag
                        )
                        FILTER LENGTH(tags) > 0
                        RETURN transcription
                """
                transcriptions = list(db.aql.execute(aql, bind_vars={
                    'sample': sample,
                    'tag_ids': tag_ids
                }))

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

