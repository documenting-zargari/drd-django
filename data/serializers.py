from rest_framework import serializers
from data.models import Category, Sample

class CategorySerializer(serializers.ModelSerializer):
    class Meta:
        model = Category
        fields = ('category_id', 'category_name', 'category_description', 
                  'parent', 'path',)

class SampleSerializer(serializers.ModelSerializer):
    class Meta:
        model = Sample
        fields = ('sample_ref', 'dialect_name',)