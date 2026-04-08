from django.http import JsonResponse
from rest_framework import status
from rest_framework.decorators import action
from rest_framework.exceptions import NotFound, ValidationError
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
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
from user.permissions import CanEditSample, IsProjectEditor


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

    Phrases are linguistic data linked to specific samples. A sample parameter
    is required to retrieve phrases.

    Available endpoints:
    - GET /phrases/?sample=<sample_ref> - List phrases for a specific sample (REQUIRED)
    - GET /phrases/<id>/ - Retrieve specific phrase by ID
    - GET /phrases/by-answer/?answer_key=<key> - Get phrases linked to an answer via phrase tags
    - POST /phrases/search/ - Search phrases across multiple samples

    Query Parameters:
    - sample (required): Sample reference (e.g., sample=AL-001)
    - answer_key (required for by-answer): Answer _key

    Examples:
    - /phrases/?sample=AL-001 - Phrases for sample AL-001
    - /phrases/123/ - Specific phrase with ID 123
    - /phrases/by-answer/?answer_key=ABC123 - Phrases linked to answer ABC123 via phrase tags
    - POST /phrases/search/ {"query": "brother", "sample_refs": ["AL-001"]}
    """

    model = Phrase
    serializer_class = PhraseSerializer
    http_method_names = ["get", "post", "patch", "head", "options"]
    permission_classes = [AllowAny]  # GET and search POST are public; PATCH uses per-action override

    EDITABLE_FIELDS = {"phrase", "english", "conjugated"}

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
        - query (required, min 2 chars): Search term
        - sample_refs (optional): List of sample refs to limit search
        - sort (optional, default 'phrase_ref'): Sort field — 'phrase_ref' or 'sample'
        - page (optional, default 1): Page number
        - page_size (optional, default 50, max 200): Results per page
        """
        query = request.data.get("query", "").strip()
        if not query or len(query) < 2:
            raise ValidationError("Query must be at least 2 characters")

        sample_refs = request.data.get("sample_refs", [])
        sort = request.data.get("sort", "phrase_ref")
        field = request.data.get("field", "both")  # 'romani', 'english', or 'both'
        page = int(request.data.get("page", 1))
        page_size = min(int(request.data.get("page_size", 50)), 200)
        offset = (page - 1) * page_size

        db = request.arangodb
        query_lower = query.lower()

        # Natural sort for phrase_ref: extract leading number, then sort by that numerically,
        # then by the remaining suffix (e.g. "80a" → 80, "a")
        natsort_expr = "TO_NUMBER(REGEX_REPLACE(phrase.phrase_ref, '[^0-9].*$', '')), REGEX_REPLACE(phrase.phrase_ref, '^[0-9]+', '')"
        sort_clauses = {
            "phrase_ref": f"SORT {natsort_expr}, phrase.sample",
            "sample": f"SORT phrase.sample, {natsort_expr}",
        }
        sort_aql = sort_clauses.get(sort, sort_clauses["phrase_ref"])

        # Resolve visible sample refs upfront (once) instead of a subquery per phrase
        if not sample_refs:
            visible_cursor = db.aql.execute(
                "FOR s IN Samples FILTER s.visible == 'Yes' RETURN s.sample_ref"
            )
            sample_refs = list(visible_cursor)

        # Uses PhraseSearch ArangoSearch view with norm_lower analyzer for fast
        # case-insensitive substring matching via LIKE with wildcards
        if field == "romani":
            like_expr = 'LIKE(phrase.phrase, CONCAT("%", @query, "%"))'
        elif field == "english":
            like_expr = 'LIKE(phrase.english, CONCAT("%", @query, "%"))'
        else:
            like_expr = 'LIKE(phrase.phrase, CONCAT("%", @query, "%")) OR LIKE(phrase.english, CONCAT("%", @query, "%"))'
        search_filter = f'SEARCH ANALYZER({like_expr}, "norm_lower")'

        # Count query
        count_aql = f"""
            FOR phrase IN PhraseSearch
                {search_filter}
                FILTER phrase.sample IN @sample_refs
                COLLECT WITH COUNT INTO total
                RETURN total
        """

        # Results query — build sample label lookup once instead of per-row subquery
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
        "migrant", "contact_languages",
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

        db.collection(self.model.collection_name).update({"_key": doc["_key"], **updates})
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
    http_method_names = ["get", "post", "patch", "head", "options"]
    permission_classes = [AllowAny]  # GET and query POST are public; PATCH uses per-action override

    # Fields that carry linguistic content — safe to edit
    EDITABLE_FIELDS = {"form", "marker", "inflection", "case_name", "comment"}

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
        PATCH /answers/{key}/ — update editable fields on an answer.
        Requires editor+ role. Editors with sample restrictions may only
        edit answers belonging to their allowed samples.

        Allowed fields: form, marker, inflection, case_name, comment
        """
        db = request.arangodb
        doc = db.collection(self.model.collection_name).get(pk)
        if not doc:
            raise NotFound(detail="Answer not found")

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
    http_method_names = ["get", "head", "options"]  # Read-only access

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

