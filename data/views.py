from django.http import JsonResponse
from rest_framework import viewsets
from rest_framework.response import Response
from rest_framework.exceptions import NotFound
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

    def get_queryset(self):
        try:
            id = self.kwargs.get('question', None)
            db = self.request.arangodb
            
            if id is None:
                raise NotFound(detail="Question ID is required to fetch answers")
            else :
                id = int(id)
            
            # get one question
            question = db.collection("ResearchQuestions").find({'id': id}).next()
            if not question:
                raise NotFound(detail=f"Question {id} not found")
            sample_ref = self.request.query_params.get('sample', None)
            # Use AQL to traverse the GivesAnswer edge and get answers, with optional sample_ref filter
            aql = "FOR v, e, p IN 1..1 OUTBOUND @question_id GivesAnswer {filter_clause} RETURN v"
            filter_clause = ""
            bind_vars = {'question_id': question['_id']}
            if sample_ref:
                filter_clause = "FILTER v.sample == @sample"
                bind_vars['sample'] = sample_ref
            aql = aql.format(filter_clause=filter_clause)
            cursor = db.aql.execute(aql, bind_vars=bind_vars)
            return [doc for doc in cursor]
        except NotFound:
            raise
        except Exception as e:
            print(f"Error fetching answers: {e}")
            return []

    def retrieve(self, request, pk=None):
        # Override retrieve to return list of answers for the question (pk is question ID)
        # Set the question parameter in kwargs for get_queryset to use
        self.kwargs['question'] = pk
        queryset = self.get_queryset()
        serializer = self.serializer_class(queryset, many=True, context={'request': request})
        return Response(serializer.data)