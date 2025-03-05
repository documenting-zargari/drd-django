from django.http import JsonResponse
from rest_framework import viewsets
from data.models import Category, Dialect, Phrase, Sample, Source
from data.serializers import CategorySerializer, DialectSerializer, PhraseSerializer, SampleListSerializer, SampleRetrieveSerializer, SourceSerializer
from roma.views import ArangoModelViewSet


class CategoryViewSet(viewsets.ModelViewSet):
    queryset = Category.objects.all()
    serializer_class = CategorySerializer

    def get_queryset(self):
        parent_category = self.request.query_params.get('parent', None)
        if parent_category is not None:
            return self.queryset.filter(parent__pk=parent_category)
        return self.queryset.filter(parent=None)

class SampleViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = Sample.objects.all()

    def get_serializer_class(self):
        if hasattr(self, 'action') and self.action == 'retrieve':
            return SampleRetrieveSerializer
        elif hasattr(self, 'action') and self.action == 'list':
            return SampleListSerializer
        return SampleListSerializer
    
    def get_queryset(self):
        return Sample.objects.filter(visible='Yes').order_by('sample_ref')


class SourceViewSet(viewsets.ModelViewSet):
    queryset = Source.objects.all()
    serializer_class = SourceSerializer

class PhraseViewSet(viewsets.ReadOnlyModelViewSet):
    queryset = Phrase.objects.all()
    serializer_class = PhraseSerializer

    def get_queryset(self):
        sample = self.kwargs.get('sample', None)
        if sample is not None:
            return self.queryset.filter(sample__pk=sample)
        return None

class DialectViewSet(ArangoModelViewSet):
    serializer_class = DialectSerializer
    model = Dialect
    http_method_names = ['get', 'head', 'options'] # prevent post

    def get_queryset(self):
        try:
            db = self.request.arangodb
            collection = db.collection(self.model.collection_name)
            cursor = collection.find({'visible': "Yes"})
            return [dialect for dialect in cursor]
        except Exception as e:
            return []

    def create(self, request):
        return JsonResponse({'error': 'Method not allowed'}, status=405)