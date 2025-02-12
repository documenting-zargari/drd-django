from collections import OrderedDict
from rest_framework import serializers
from data.models import Category, Dialect, Phrase, Sample, Source, Translation
from roma.serializers import ArangoModelSerializer

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
    translation = serializers.CharField(source='translation.english')
    # translation = TranslationSerializer(read_only=True)
    class Meta:
        model = Phrase
        fields = ['id', 'sample', 'phrase', 'translation',]


    def to_representation(self, instance):
        result = super().to_representation(instance)
        return OrderedDict([(key, result[key]) for key in result if result[key] is not None])
        # return result['phrase']

class DialectSerializer(ArangoModelSerializer):

        sample_ref = serializers.CharField(source='ref')
        class Meta:
            model = Dialect
            fields = ['sample_ref', 'source_type', 'dialect_group ', 
                      'self_attrib_name', 'dialect_name', 'location ', 
                      'country_code', 'live ', 'longitude', 'latitude', 
                      'visible', 'migrant', 'sample_ref', 
                      ]
