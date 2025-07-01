from django.urls import path, include
from data import views
from rest_framework import routers

router = routers.DefaultRouter()
router.register(r'categories', views.CategoryViewSet, basename='categories')
router.register(r'phrases', views.PhraseViewSet, basename='phrases-all')
router.register(r'phrases/(?P<sample>[^/.]+)', views.PhraseViewSet, basename='phrases')
router.register(r'samples', views.SampleViewSet, basename='samples')
router.register(r'answers', views.AnswerViewSet, basename='answers')

urlpatterns = [
    path('', include(router.urls)),
]