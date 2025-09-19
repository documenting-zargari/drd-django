from rest_framework import serializers

from data.models import (
    Answer,
    Category,
    Phrase,
    Sample,
    Source,
    Transcription,
    Translation,
    View,
)
from roma.serializers import ArangoModelSerializer


class CategorySerializer(ArangoModelSerializer):
    id = serializers.IntegerField()
    parent_id = serializers.IntegerField()
    has_children = serializers.SerializerMethodField()
    drill = serializers.SerializerMethodField()
    hierarchy = serializers.SerializerMethodField()

    class Meta:
        model = Category
        fields = (
            "id",
            "name",
            "parent_id",
            "hierarchy",
            "hierarchy_ids",
            "has_children",
            "drill",
            "path",
        )

    def get_hierarchy(self, obj):
        # this contains a json string - return a list
        # Handle both dict objects (from ArangoDB) and model objects
        if isinstance(obj, dict):
            hierarchy = obj.get("hierarchy", [])
        else:
            hierarchy = getattr(obj, "hierarchy", [])

        if isinstance(hierarchy, str):
            try:
                hierarchy = eval(hierarchy)  # Convert string representation to list
            except Exception as e:
                print(f"Error parsing hierarchy: {e}")
                hierarchy = []
        return hierarchy

    def get_has_children(self, obj):
        request = self.context.get("request")
        if not request or not hasattr(request, "arangodb"):
            return False

        # Handle both dict objects (from ArangoDB) and model objects
        if isinstance(obj, dict):
            obj_id = obj["id"]
        else:
            obj_id = obj.id

        db = request.arangodb
        collection = db.collection(self.Meta.model.collection_name)
        cursor = collection.find({"parent_id": obj_id}, limit=1)
        return len([child for child in cursor]) > 0

    def get_drill(self, obj):
        request = self.context.get("request")
        if not request:
            return None

        # Only include URL if category has children
        if self.get_has_children(obj):
            return request.build_absolute_uri(f"/categories/?parent_id={obj['id']}")
        return None

    def to_representation(self, instance):
        result = super().to_representation(instance)
        # Remove drill field if it's null
        if result.get("drill") is None:
            result.pop("drill", None)
        
        # Remove path field if it's not present in the original instance
        # Handle both dict objects (from ArangoDB) and model objects
        if isinstance(instance, dict):
            path_value = instance.get("path")
        else:
            path_value = getattr(instance, "path", None)
        
        if path_value is None:
            result.pop("path", None)
        
        return result


class TranslationSerializer(serializers.ModelSerializer):
    class Meta:
        model = Translation
        fields = [
            "id",
            "conjugated",
            "english",
        ]


class PhraseSerializer(ArangoModelSerializer):
    has_recording = serializers.SerializerMethodField(required=False)

    class Meta:
        model = Phrase
        fields = [
            "phrase",
            "phrase_ref",
            "conjugated",
            "english",
            "has_recording",
        ]

    def get_has_recording(self, obj):
        # Handle both dict objects (from ArangoDB) and model objects
        if isinstance(obj, dict):
            return obj.get("has_recording", False)
        return getattr(obj, "has_recording", False)

    def to_representation(self, instance):
        # Return all attributes from the ArangoDB document, excluding certain fields
        exclude_fields = ["_rev", "_id"]  # Add fields you want to exclude
        result = {k: v for k, v in instance.items() if k not in exclude_fields}
        return result


class SampleSerializer(ArangoModelSerializer):
    coordinates = serializers.SerializerMethodField()
    contact_languages = serializers.SerializerMethodField()
    sources = serializers.SerializerMethodField()

    class Meta:
        model = Sample
        fields = [
            "sample_ref",
            "source_type",
            "dialect_name",
            "self_attrib_name",
            "dialect_group_name",
            "location",
            "country_code",
            "live",
            "coordinates",
            "visible",
            "migrant",
            "contact_languages",
            "sources",
        ]

    def get_coordinates(self, obj):
        # Handle both dict objects (from ArangoDB) and model objects
        if isinstance(obj, dict):
            return obj.get("coordinates", None)
        else:
            return getattr(obj, "coordinates", None)

    def get_contact_languages(self, obj):
        # Handle both dict objects (from ArangoDB) and model objects
        if isinstance(obj, dict):
            return obj.get("contact_languages", None)
        else:
            return getattr(obj, "contact_languages", None)

    def get_sources(self, obj):
        # Handle both dict objects (from ArangoDB) and model objects
        if isinstance(obj, dict):
            sources = obj.get("sources", [])
            # Clean up each source by removing ArangoDB internal fields
            exclude_fields = ["_rev", "_key", "_id"]
            return [{k: v for k, v in source.items() if k not in exclude_fields} for source in sources]
        else:
            return getattr(obj, "sources", [])

    def to_representation(self, instance):
        result = super().to_representation(instance)

        # Remove contact_languages from list view only, keep it for detail view
        view = self.context.get("view")
        if view and hasattr(view, "action") and view.action == "list":
            result.pop("contact_languages", None)

        return result


class SourceSerializer(ArangoModelSerializer):
    class Meta:
        model = Source
        fields = "__all__"


class AnswerSerializer(ArangoModelSerializer):
    class Meta:
        model = Answer
        fields = "__all__"

    def to_representation(self, instance):
        # Handle both dict objects (from ArangoDB) and model objects
        if isinstance(instance, dict):
            exclude_fields = ["_rev", "_id"]
            return {k: v for k, v in instance.items() if k not in exclude_fields}
        else:
            # For model instances, use the parent serializer's method
            return super().to_representation(instance)


class ViewSerializer(ArangoModelSerializer):
    parent_category = serializers.SerializerMethodField()

    class Meta:
        model = View
        fields = ["filename", "content", "parent_id", "parent_category"]

    def get_parent_category(self, obj):
        request = self.context.get("request")
        if not request:
            raise serializers.ValidationError(
                "Request context is required for parent category lookup."
            )
        if not request or not hasattr(request, "arangodb"):
            raise serializers.ValidationError(
                "ArangoDB connection is required in the request context."
            )

        parent_id = (
            obj.get("parent_id")
            if isinstance(obj, dict)
            else getattr(obj, "parent_id", None)
        )
        if not parent_id:
            raise serializers.ValidationError(
                "Parent ID is required to fetch parent category."
            )

        db = request.arangodb
        if not db:
            raise serializers.ValidationError("ArangoDB connection is not available.")
        collection = db.collection(Category.collection_name)
        cursor = collection.find({"id": parent_id}, limit=1)
        docs = list(cursor)

        if docs:
            return CategorySerializer(docs[0], context={"request": request}).data
        return None

    def to_representation(self, instance):
        result = super().to_representation(instance)
        # Handle both dict objects (from ArangoDB) and model objects
        if isinstance(instance, dict):
            exclude_fields = ["_rev", "_key"]
            result = {k: v for k, v in instance.items() if k not in exclude_fields}
        return result


class TranscriptionSerializer(ArangoModelSerializer):
    class Meta:
        model = Transcription
        fields = "__all__"

    def to_representation(self, instance):
        # Return all attributes from the ArangoDB document, excluding certain fields
        exclude_fields = ["_rev", "_key"]
        return {k: v for k, v in instance.items() if k not in exclude_fields}
