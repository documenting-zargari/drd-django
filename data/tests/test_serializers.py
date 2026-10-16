from django.test import SimpleTestCase

from data.models import Category
from data.serializers import AnswerSerializer, ResearchQuestionSerializer


class ResearchQuestionSerializerTests(SimpleTestCase):
    DOC = {
        "_key": "7",
        "_id": "Categories/7",
        "_rev": "_abc",
        "id": 7,
        "name": "Copula",
        "parent_id": 3,
        "hierarchy": ["Grammar", "Copula"],
        "hierarchy_ids": [3, 7],
        "is_leaf": True,
    }
    EXPECTED = {
        "_key": "7",
        "id": 7,
        "name": "Copula",
        "parent_id": 3,
        "hierarchy": ["Grammar", "Copula"],
        "hierarchy_ids": [3, 7],
    }

    def test_serializes_raw_dict(self):
        # batch/search/retrieve hand the serializer raw AQL dicts.
        self.assertEqual(ResearchQuestionSerializer(dict(self.DOC)).data, self.EXPECTED)

    def test_serializes_model_instance(self):
        # Regression: model instances (rather than raw dicts) once crashed
        # GET /research-questions/ with "object has no attribute 'items'".
        instance = Category(**self.DOC)
        self.assertEqual(ResearchQuestionSerializer(instance).data, self.EXPECTED)


class AnswerSerializerTests(SimpleTestCase):
    def test_hides_legacy_origin_language_backup(self):
        doc = {
            "_key": "1",
            "_id": "Answers/1",
            "_rev": "_abc",
            "sample": "BG-007",
            "source": "Current-L2",
            "language": "Bulgarian",
            "legacy_origin_language": {"language": "Bulgarian"},
        }
        self.assertEqual(
            AnswerSerializer(doc).data,
            {"_key": "1", "sample": "BG-007", "source": "Current-L2", "language": "Bulgarian"},
        )
