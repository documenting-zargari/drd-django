from django.http import HttpResponse, JsonResponse
from django.shortcuts import render
from rest_framework import viewsets
from data.models import Category, Sample
from data.serializers import CategorySerializer, SampleSerializer

class CategoryViewSet(viewsets.ModelViewSet):
    queryset = Category.objects.all()
    serializer_class = CategorySerializer

def index(request):
    return JsonResponse({ "message": "Hello, world!" })

class SampleViewSet(viewsets.ModelViewSet):
    queryset = Sample.objects.all()
    serializer_class = SampleSerializer