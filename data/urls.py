from django.urls import include, path
from rest_framework import routers

from data import views

router = routers.DefaultRouter()
router.register(r"categories", views.CategoryViewSet, basename="categories")
router.register(r"phrases", views.PhraseViewSet, basename="phrases")
router.register(r"samples", views.SampleViewSet, basename="samples")
router.register(r"answers", views.AnswerViewSet, basename="answers")
router.register(r"views", views.ViewViewSet, basename="views")
router.register(
    r"transcriptions", views.TranscriptionViewSet, basename="transcriptions"
)

urlpatterns = [
    path("", include(router.urls)),
]
