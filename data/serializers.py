from collections import OrderedDict
from rest_framework import serializers
from data.models import Category, Phrase, Sample, Source, Translation

class CategorySerializer(serializers.ModelSerializer):
    class Meta:
        model = Category
        fields = ('category_id', 'category_name', 'category_description', 
                  'parent', 'path',)

class SampleListSerializer(serializers.ModelSerializer):
    class Meta:
        model = Sample
        fields = ('sample_ref', 'dialect_name', 'visible')

class SampleRetrieveSerializer(serializers.ModelSerializer):
    class Meta:
        model = Sample
        exclude = ('visible', 'live')

class SourceSerializer(serializers.ModelSerializer): 
    class Meta:
        model = Source
        fields = '__all__'
    
    def to_representation(self, instance):
        result = super().to_representation(instance)
        return OrderedDict([(key, result[key]) for key in result if result[key] is not None])

class TranslationSerializer(serializers.ModelSerializer):
    class Meta:
        model = Translation
        fields = ['id', 'conjugated', 'english',]

class PhraseSerializer(serializers.ModelSerializer):
    class Meta:
        model = Phrase
        fields = ['id', 'sample', 'phrase', 'translation',]

    translation = TranslationSerializer(read_only=True)

    def to_representation(self, instance):
        result = super().to_representation(instance)
        return OrderedDict([(key, result[key]) for key in result if result[key] is not None])
        # return result['phrase']
    