from django.http import JsonResponse
from rest_framework import viewsets
from rest_framework.response import Response
from rest_framework.exceptions import NotFound
from rest_framework.decorators import action
from data.models import Answer, Category, Phrase, Sample, Source
from data.serializers import AnswerSerializer, CategorySerializer, SampleSerializer, PhraseSerializer, SourceSerializer
from roma.views import ArangoModelViewSet


class CategoryViewSet(ArangoModelViewSet):
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
        queryset = self.get_queryset()
        serializer = self.serializer_class(queryset, many=True, context={'request': request}) # serializer needs request
        return Response(serializer.data)
    
    def retrieve(self, request, pk=None):
        instance = self.get_object(pk)
        serializer = self.serializer_class(instance, context={'request': request})
        return Response(serializer.data)
    
    @action(detail=False, methods=['get'])
    def search(self, request):
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
        # Override retrieve to return list of phrases for the sample (pk is the sample_ref)
        # Set the sample parameter in kwargs for get_queryset to use
        self.kwargs['sample'] = pk
        queryset = self.get_queryset()
        serializer = self.serializer_class(queryset, many=True, context={'request': request})
        return Response(serializer.data)

class SampleViewSet(ArangoModelViewSet):
    serializer_class = SampleSerializer
    model = Sample
    http_method_names = ['get', 'head', 'options'] # prevent post
    
    def get_object(self, pk):
        # Override to use sample_ref instead of _key
        instance = self.model.get_by_field('sample_ref', pk)
        if not instance:
            raise NotFound(detail="Sample not found")
        return instance

    def get_queryset(self):
        try:
            db = self.request.arangodb
            collection = db.collection(self.model.collection_name)
            cursor = collection.find({'visible': "Yes"})
            return [sample for sample in cursor]
        except Exception as e:
            return []

    def create(self, request):
        return JsonResponse({'error': 'Method not allowed'}, status=405)
    

class SourceViewSet(ArangoModelViewSet):
    serializer_class = SourceSerializer
    model = Source
    http_method_names = ['get', 'head', 'options']  # prevent post

class AnswerViewSet(ArangoModelViewSet):
    serializer_class = AnswerSerializer
    model = Answer
    http_method_names = ['get', 'head', 'options']  # exclude PUT, POST, DELETE

    def get_object(self, pk):
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
        """Get answers filtered by question IDs and optionally by sample references"""
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