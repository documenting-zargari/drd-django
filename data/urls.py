from django.urls import path
from data import views

urlpatterns = [
    path('categories', views.CategoryViewSet.as_view({'get': 'list'}), name='categories-list'),
    path('samples', views.SampleViewSet.as_view({'get': 'list'}), name='samples-list'),
    path('', views.index, name='index'),
]