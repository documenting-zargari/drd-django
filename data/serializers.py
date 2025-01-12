from rest_framework import serializers
from data.models import Category, Sample

class CategorySerializer(serializers.ModelSerializer):
    class Meta:
        model = Category
        fields = '__all__'

class SampleSerializer(serializers.ModelSerializer):
    class Meta:
        model = Sample
        fields = ('sample_ref', 'dialect_name',)