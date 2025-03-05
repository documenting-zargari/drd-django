from django.urls import path, include
from data import views
from rest_framework import routers

router = routers.DefaultRouter()
router.register(r'samples', views.SampleViewSet)
router.register(r'categories', views.CategoryViewSet)
router.register(r'phrases/(?P<sample>[^/.]+)', views.PhraseViewSet, basename='phrases')
router.register(r'dialects', views.DialectViewSet, basename='dialects')

urlpatterns = [
    path('', include(router.urls)),
]