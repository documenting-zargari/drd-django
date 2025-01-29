from django.urls import path
from data import views

urlpatterns = [
    path('categories', views.CategoryViewSet.as_view({'get': 'list'}), name='categories-list'),
    path('samples', views.SampleViewSet.as_view({'get': 'list'}), name='samples-list'),
    path("samples/<str:pk>", views.SampleViewSet.as_view({'get': 'retrieve'}), name="sample-detail"),
    path('sources', views.SourceViewSet.as_view({'get': 'list'}), name='sources-list'),
    path("phrases/<str:sample>", views.PhraseViewSet.as_view({'get': 'list'}), name="phrases-list"),
]