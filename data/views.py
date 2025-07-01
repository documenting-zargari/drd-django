from django.http import JsonResponse
from rest_framework import viewsets
from rest_framework.response import Response
from rest_framework.exceptions import NotFound
from rest_framework.decorators import action
from data.models import Answer, Category, Phrase, Sample, Source
from data.serializers import AnswerSerializer, CategorySerializer, SampleSerializer, PhraseSerializer, SourceSerializer
from roma.views import ArangoModelViewSet


class CategoryViewSet(ArangoModelViewSet):
    """
    API endpoint for browsing categories in a hierarchical structure.
    
    Categories are organized in a tree structure with parent-child relationships.
    Use the parent_id parameter to navigate the hierarchy.
    
    Available endpoints:
    - GET /categories/ - List root categories (parent_id=1 by default)
    - GET /categories/?parent_id=<id> - List child categories
    - GET /categories/<id>/ - Retrieve specific category
    - GET /categories/search/?q=<term> - Search categories by name
    """
    model = Category
    serializer_class = CategorySerializer
    http_method_names = ['get', 'head', 'options'] # prevent post

    def get_queryset(self):
        parent_id = self.request.query_params.get('parent_id')
        db = self.request.arangodb
        collection = db.collection(self.model.collection_name)
        exclude_ids = [2, 3]
        id = int(parent_id) if parent_id else 1
        categories_cursor = collection.find({'parent_id': id})
        return [c for c in categories_cursor if c['id'] not in exclude_ids]


    def get_object(self, pk):
        # Override to use id instead of _key and return raw dict like list method
        db = self.request.arangodb
        collection = db.collection(self.model.collection_name)
        if isinstance(pk, str) and pk.isdigit():
            pk = int(pk)
        cursor = collection.find({'id': pk}, limit=1)
        docs = list(cursor)
        if not docs:
            raise NotFound(detail="Category not found")
        return docs[0]
    
    def list(self, request, *args, **kwargs):
        """
        List categories by parent relationship.
        
        Query Parameters:
        - parent_id (optional): ID of parent category to list children for.
                               Defaults to 1 (root categories).
        
        Example:
        - /categories/ - Lists root categories
        - /categories/?parent_id=5 - Lists child categories of category 5
        
        Returns categories excluding system categories (IDs 2, 3).
        """
        queryset = self.get_queryset()
        serializer = self.serializer_class(queryset, many=True, context={'request': request}) # serializer needs request
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
        serializer = self.serializer_class(instance, context={'request': request})
        return Response(serializer.data)
    
    @action(detail=False, methods=['get'])
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
        query = request.query_params.get('q', '').strip()
        if not query or len(query) < 2:
            return Response([])
        
        db = request.arangodb
        if not db:
            return Response({'error': 'Database not available'}, status=500)

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
            "is_leaf": doc.is_leaf 
        }
        """

        try:
            cursor = db.aql.execute(aql_query, bind_vars={'search_pattern': search_pattern})
            results = []
            for doc in cursor:
                # Parse hierarchy if it's stored as string
                hierarchy = doc.get('hierarchy', [])
                if isinstance(hierarchy, str):
                    try:
                        hierarchy = eval(hierarchy)
                    except:
                        hierarchy = []
                results.append({
                    'id': doc['id'],
                    'name': doc['name'],
                    'hierarchy': hierarchy if len(hierarchy) > 2 else [],
                    'parent_id': doc['parent_id'],
                    'has_children': not doc.get('is_leaf', False),
                })
            
            return Response(results)
        except Exception as e:
            return Response({'error': f'Search failed: {str(e)}'}, status=500)

# class SourceViewSet(viewsets.ModelViewSet):
#     queryset = Source.objects.all()
#     serializer_class = SourceSerializer
    
    

class PhraseViewSet(viewsets.ReadOnlyModelViewSet):
    """
    API endpoint for retrieving phrases associated with samples.
    
    Phrases are linguistic data linked to specific samples. A sample parameter
    is required to retrieve phrases.
    
    Available endpoints:
    - GET /phrases/<sample_ref>/ - Retrieve all phrases for a specific sample
    
    Note: Direct listing of all phrases is not supported - sample parameter is required.
    """
    serializer_class = PhraseSerializer
    model = Phrase

    def get_queryset(self):
        try:
            sample = self.kwargs.get('sample', None)
            db = self.request.arangodb
            collection = db.collection(Phrase.collection_name)
            if sample is None:
                # Return no phrases when no sample parameter
                raise NotFound(detail="Sample parameter is required to fetch phrases")
            else:
                cursor = collection.find({'sample': sample})
            return [phrase for phrase in cursor]
        except NotFound:
            # Re-raise NotFound exceptions so they are handled by DRF
            raise
        except Exception as e:
            print(f"Error fetching phrases: {e}")
            return []
    
    def retrieve(self, request, pk=None):
        """
        Retrieve all phrases for a specific sample.
        
        Parameters:
        - pk: Sample reference (sample_ref) - REQUIRED
        
        Example:
        - /phrases/AL-001/ - Retrieves all phrases for sample AL-001
        
        Returns a list of phrases associated with the specified sample,
        including phrase text and translation information.
        """
        # Override retrieve to return list of phrases for the sample (pk is the sample_ref)
        # Set the sample parameter in kwargs for get_queryset to use
        self.kwargs['sample'] = pk
        queryset = self.get_queryset()
        serializer = self.serializer_class(queryset, many=True, context={'request': request})
        return Response(serializer.data)

class SampleViewSet(ArangoModelViewSet):
    """
    API endpoint for retrieving linguistic samples.
    
    Samples are audio/text recordings with metadata. Only visible samples
    are returned in listings.
    
    Available endpoints:
    - GET /samples/ - List all visible samples
    - GET /samples/<sample_ref>/ - Retrieve specific sample by reference
    
    Samples are identified by their sample_ref (not numeric ID).
    """
    serializer_class = SampleSerializer
    model = Sample
    http_method_names = ['get', 'head', 'options'] # prevent post
    
    def get_object(self, pk):
        """
        Retrieve a specific sample by sample reference.
        
        Parameters:
        - pk: Sample reference (sample_ref) - e.g., 'AL-001'
        
        Example:
        - /samples/AL-001/ - Retrieves sample with reference AL-001
        
        Returns complete sample metadata including source information.
        """
        # Override to use sample_ref instead of _key
        instance = self.model.get_by_field('sample_ref', pk)
        if not instance:
            raise NotFound(detail="Sample not found")
        return instance

    def get_queryset(self):
        """
        Retrieve all visible samples.
        
        Returns only samples marked as visible='Yes' in the database.
        Samples with other visibility settings are excluded from listings.
        """
        try:
            db = self.request.arangodb
            collection = db.collection(self.model.collection_name)
            cursor = collection.find({'visible': "Yes"})
            return [sample for sample in cursor]
        except Exception as e:
            return []

    def list(self, request, *args, **kwargs):
        """
        List all visible samples with proper context for serializer.
        
        Passes view context to serializer so it can detect list vs detail operations
        and conditionally exclude contact_languages from list output.
        """
        queryset = self.get_queryset()
        serializer = self.serializer_class(queryset, many=True, context={'request': request, 'view': self})
        return Response(serializer.data)

    def create(self, request):
        return JsonResponse({'error': 'Method not allowed'}, status=405)
    

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
    http_method_names = ['get', 'head', 'options']  # prevent post

class AnswerViewSet(ArangoModelViewSet):
    """
    API endpoint for retrieving answers to research questions.
    
    Answers are linked to specific research questions and can be filtered
    by sample references. At least one question ID is required for listings.
    
    Available endpoints:
    - GET /answers/?q=<id>&q=<id> - List answers for specific questions (REQUIRED)
    - GET /answers/?q=<id>&s=<ref>&s=<ref> - Filter by questions and samples
    - GET /answers/<id>/ - Retrieve specific answer by answer ID
    
    Query Parameters:
    - q (required): Question ID(s) - multiple values allowed (e.g., ?q=1&q=2&q=3)
    - s (optional): Sample reference(s) - multiple values allowed (e.g., ?s=AL-001&s=AL-002)
    
    Examples:
    - /answers/?q=1 - Answers for question 1
    - /answers/?q=1&q=2 - Answers for questions 1 and 2
    - /answers/?q=1&s=AL-001 - Answers for question 1 from sample AL-001
    - /answers/123/ - Specific answer with ID 123
    """
    serializer_class = AnswerSerializer
    model = Answer
    http_method_names = ['get', 'head', 'options']  # exclude PUT, POST, DELETE

    def get_object(self, pk):
        """
        Retrieve a specific answer by answer ID.
        
        Parameters:
        - pk: Answer ID (integer)
        
        Example:
        - /answers/123/ - Retrieves answer with ID 123
        
        Returns complete answer data including question context.
        """
        # Override to use answer ID instead of _key
        db = self.request.arangodb
        collection = db.collection(self.model.collection_name)
        if isinstance(pk, str) and pk.isdigit():
            pk = int(pk)
        cursor = collection.find({'id': pk}, limit=1)
        docs = list(cursor)
        if not docs:
            raise NotFound(detail="Answer not found")
        return docs[0]

    def get_queryset(self):
        """
        Get answers filtered by question IDs and optionally by sample references.
        
        Query Parameters:
        - q (required): Question ID(s) - multiple values allowed
        - s (optional): Sample reference(s) - multiple values allowed
        
        Examples:
        - ?q=1&q=2 - Answers for questions 1 and 2
        - ?q=1&s=AL-001&s=AL-002 - Answers for question 1 from specific samples
        
        Returns answers with question_id added for context.
        Raises 404 if no question IDs provided or if invalid IDs/samples specified.
        """
        try:
            question_ids = self.request.GET.getlist('q')
            sample_refs = self.request.GET.getlist('s')
            
            if not question_ids:
                raise NotFound(detail="At least one question ID (q parameter) is required")
            
            return self.get_answers_for_questions(question_ids, sample_refs)
        except NotFound:
            raise
        except Exception as e:
            print(f"Error fetching answers: {e}")
            return []

    def validate_questions(self, question_ids):
        """Validate all question IDs exist"""
        db = self.request.arangodb
        aql = "FOR q IN ResearchQuestions FILTER q.id IN @question_ids RETURN q.id"
        cursor = db.aql.execute(aql, bind_vars={'question_ids': question_ids})
        existing_questions = [qid for qid in cursor]
        missing_questions = set(question_ids) - set(existing_questions)
        if missing_questions:
            raise NotFound(detail=f"Questions not found: {sorted(missing_questions)}")

    def validate_samples(self, sample_refs):
        """Validate all sample references exist"""
        db = self.request.arangodb
        aql = "FOR s IN Samples FILTER s.sample_ref IN @sample_refs RETURN s.sample_ref"
        cursor = db.aql.execute(aql, bind_vars={'sample_refs': sample_refs})
        existing_samples = [ref for ref in cursor]
        missing_samples = set(sample_refs) - set(existing_samples)
        if missing_samples:
            raise NotFound(detail=f"Samples not found: {sorted(missing_samples)}")

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
            sample_filter = ""
            bind_vars = {'question_ids': question_ids}
            
            if sample_refs:
                sample_filter = "FILTER answer.sample IN @samples"
                bind_vars['samples'] = sample_refs

            aql = f"""
            FOR question IN ResearchQuestions
              FILTER question.id IN @question_ids
              FOR answer IN 1..1 OUTBOUND question GivesAnswer
                {sample_filter}
                RETURN MERGE(answer, {{question_id: question.id}})
            """
            
            cursor = db.aql.execute(aql, bind_vars=bind_vars)
            answers = [doc for doc in cursor]
            
            return answers
        except NotFound:
            raise
        except Exception as e:
            print(f"Error fetching answers: {e}")
            return []