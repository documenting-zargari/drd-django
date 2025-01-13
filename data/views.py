from rest_framework import viewsets
from data.models import Category, Sample
from data.serializers import CategorySerializer, SampleListSerializer, SampleRetrieveSerializer

class CategoryViewSet(viewsets.ModelViewSet):
    queryset = Category.objects.all()
    serializer_class = CategorySerializer

    def get_queryset(self):
        parent_category = self.request.query_params.get('parent', None)
        if parent_category is not None:
            return self.queryset.filter(parent__pk=parent_category)
        return self.queryset.filter(parent=None)

class SampleViewSet(viewsets.ModelViewSet):
    queryset = Sample.objects.all()

    def get_serializer_class(self):
        if hasattr(self, 'action') and self.action == 'retrieve':
            return SampleRetrieveSerializer
        elif hasattr(self, 'action') and self.action == 'list':
            return SampleListSerializer
        return super().get_serializer_class()
