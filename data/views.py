from django.http import HttpResponse, JsonResponse
from django.shortcuts import render
from rest_framework import viewsets
from data.models import Category, Sample
from data.serializers import CategorySerializer, SampleSerializer

class CategoryViewSet(viewsets.ModelViewSet):
    queryset = Category.objects.all()
    serializer_class = CategorySerializer

    def get_queryset(self):
        parent_category = self.request.query_params.get('parent', None)
        if parent_category is not None:
            return self.queryset.filter(parent__pk=parent_category)
        return self.queryset.filter(parent=None)

def index(request):
    return JsonResponse({ "message": "Hello, world!" })

class SampleViewSet(viewsets.ModelViewSet):
    queryset = Sample.objects.all()
    serializer_class = SampleSerializer