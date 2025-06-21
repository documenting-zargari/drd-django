from collections import OrderedDict
from rest_framework import serializers
from data.models import Category, Phrase, Sample, Source, Translation
from roma.serializers import ArangoModelSerializer

class CategorySerializer(serializers.ModelSerializer):
    class Meta:
        model = Category
        fields = ('category_id', 'category_name', 'category_description', 
                  'parent', 'path',)

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
    translation = serializers.CharField(source='translation.english')
    phrase_ref = serializers.CharField(source='translation.phrase_ref')
    # translation = TranslationSerializer(read_only=True)
    class Meta:
        model = Phrase
        fields = ['phrase_ref', 'sample', 'phrase', 'translation',]


    def to_representation(self, instance):
        result = super().to_representation(instance)
        return OrderedDict([(key, result[key]) for key in result if result[key] is not None])
        # return result['phrase']

class SampleSerializer(ArangoModelSerializer):
    class Meta:
        model = Sample
        fields = [
            'sample_ref', 'source_type', 'dialect_group',
            'self_attrib_name', 'dialect_name', 'location',
            'country_code', 'live', 'coordinates',
            'visible', 'migrant', 'dialect_group', 'contact_languages',
        ]
