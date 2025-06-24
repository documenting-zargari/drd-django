from collections import OrderedDict
from rest_framework import serializers
from data.models import Answer, Category, Phrase, Sample, Source, Translation
from roma.serializers import ArangoModelSerializer

class CategorySerializer(ArangoModelSerializer):
    has_children = serializers.SerializerMethodField()
    drill = serializers.SerializerMethodField()
    hierarchy = serializers.SerializerMethodField()
    
    class Meta:
        model = Category
        fields = ('id', 'name', 'parent_id', 'hierarchy', 'hierarchy_ids', 'has_children', 'drill')
    
    def get_hierarchy(self, obj):
        # this contains a json string - return a list
        hierarchy = obj.get('hierarchy', [])
        if isinstance(hierarchy, str):
            try:
                hierarchy = eval(hierarchy)  # Convert string representation to list
            except Exception as e:
                print(f"Error parsing hierarchy: {e}")
                hierarchy = []
        return hierarchy
    
    def get_has_children(self, obj):
        request = self.context.get('request')
        if not request or not hasattr(request, 'arangodb'):
            return False
        
        db = request.arangodb
        collection = db.collection(self.Meta.model.collection_name)
        cursor = collection.find({'parent_id': obj['id']}, limit=1)
        return len([child for child in cursor]) > 0
    
    def get_drill(self, obj):
        request = self.context.get('request')
        if not request:
            return None
        
        # Only include URL if category has children
        if self.get_has_children(obj):
            return request.build_absolute_uri(f"/categories/?parent_id={obj['id']}")
        return None
    
    def to_representation(self, instance):
        result = super().to_representation(instance)
        # Remove drill field if it's null
        if result.get('drill') is None:
            result.pop('drill', None)
        return result

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

class PhraseSerializer(ArangoModelSerializer):
    class Meta:
        model = Phrase
        fields = ['phrase', 'phrase_ref', 'conjugated', 'english',]

    def to_representation(self, instance):
        # Return all attributes from the ArangoDB document, excluding certain fields
        exclude_fields = ['_rev', '_key']  # Add fields you want to exclude
        return {k: v for k, v in instance.items() if k not in exclude_fields}

class SampleSerializer(ArangoModelSerializer):
    coordinates = serializers.SerializerMethodField()
    contact_languages = serializers.SerializerMethodField()
    class Meta:
        model = Sample
        fields = [
            'sample_ref', 'source_type', 'dialect_group',
            'self_attrib_name', 'dialect_name', 'location',
            'country_code', 'live', 'coordinates',
            'visible', 'migrant', 'dialect_group', 'contact_languages',
        ]
    def get_coordinates(self, obj):
        return getattr(obj, 'coordinates', None)
    
    def get_contact_languages(self, obj):
        return getattr(obj, 'contact_languages', None)

class SourceSerializer(ArangoModelSerializer):
    class Meta:
        model = Source
        fields = '__all__'
        
class AnswerSerializer(ArangoModelSerializer):
    class Meta:
        model = Answer
        fields = '__all__'
    
    def to_representation(self, instance):
        # Return all attributes from the ArangoDB document, excluding certain fields
        exclude_fields = ['_rev', '_key']  # Add fields you want to exclude
        return {k: v for k, v in instance.items() if k not in exclude_fields}