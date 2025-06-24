from django.http import JsonResponse
from rest_framework import viewsets
from rest_framework.response import Response
from rest_framework.exceptions import NotFound
from data.models import Category, Phrase, Sample, Source
from data.serializers import CategorySerializer, SampleSerializer, PhraseSerializer, SourceSerializer
from roma.views import ArangoModelViewSet


class CategoryViewSet(ArangoModelViewSet):
    model = Category
    serializer_class = CategorySerializer
    http_method_names = ['get', 'head', 'options'] # prevent post

    def get_queryset(self):
        parent_id = self.request.query_params.get('parent_id')
        db = self.request.arangodb
        collection = db.collection(self.model.collection_name)
        
        if parent_id:
            # Filter by parent_id query parameter
            cursor = collection.find({'parent_id': int(parent_id)})
        else:
            # Default to root categories (parent_id = 1)
            cursor = collection.find({'parent_id': 1})
        
        return [category for category in cursor]

    def get_object(self, pk):
        # Override to use id instead of _key
        instance = self.model.get_by_field('id', pk)
        if not instance:
            raise NotFound(detail="Category not found")
        return instance
    
    def list(self, request, *args, **kwargs):
        queryset = self.get_queryset()
        serializer = self.serializer_class(queryset, many=True, context={'request': request})
        return Response(serializer.data)

class SourceViewSet(viewsets.ModelViewSet):
    queryset = Source.objects.all()
    serializer_class = SourceSerializer
    
    

class PhraseViewSet(viewsets.ReadOnlyModelViewSet):
    serializer_class = PhraseSerializer
    model = Phrase

    def get_queryset(self):
        try:
            sample = self.kwargs.get('sample', None)
            if sample is not None:
                breakpoint()
                db = self.request.arangodb
                collection = db.collection(Phrase.collection_name)
                cursor = collection.find({'sample': sample})
                return [phrase for phrase in cursor]
            return []
        except Exception as e:
            print(f"Error fetching phrases: {e}")
            return []

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
    
    