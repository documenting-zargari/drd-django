from django.http import JsonResponse
from rest_framework import viewsets
from data.models import Category, Dialect, Phrase, Sample, Source
from data.serializers import CategorySerializer, DialectSerializer, PhraseSerializer, SampleListSerializer, SampleRetrieveSerializer, SourceSerializer

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
        sample = self.request.query_params.get('sample', None)
        print("request", self.request.query_params)
        print("sample", sample)
        if sample is not None:
            return self.queryset.filter(sample__pk=sample)
        return None
    
def get_dialects(request):
    db = request.arangodb
    collection = db.collection('Dialects')
    filter_param = request.GET.get('filter', None)
    if filter_param:
        cursor = collection.find({'country_code': filter_param})
    else:
        cursor = collection.all()
    dialects = [dialect for dialect in cursor]
    return JsonResponse(dialects, safe=False)

# class DialectViewSet(viewsets.ViewSet):
#     serializer_class = DialectSerializer
    
