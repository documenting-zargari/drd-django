"""
URL configuration for roma project.

The `urlpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/5.1/topics/http/urls/
Examples:
Function views
    1. Add an import:  from my_app import views
    2. Add a URL to urlpatterns:  path('', views.home, name='home')
Class-based views
    1. Add an import:  from other_app.views import Home
    2. Add a URL to urlpatterns:  path('', Home.as_view(), name='home')
Including another URLconf
    1. Import the include() function: from django.urls import include, path
    2. Add a URL to urlpatterns:  path('blog/', include('blog.urls'))
"""

from django.contrib import admin
from django.urls import include, path
from rest_framework.authtoken.views import obtain_auth_token
from rest_framework import routers
from rest_framework.decorators import api_view
from rest_framework.response import Response
from rest_framework.reverse import reverse
import data.urls
import user.urls

router = routers.DefaultRouter()
router.registry.extend(data.urls.router.registry)
router.registry.extend(user.urls.router.registry)

@api_view(['GET'])
def api_root(request, format=None):
    """
    Roma API Root
    
    This API provides access to linguistic data and research materials.
    Authentication is required for most endpoints using Token Authentication.
    """
    return Response({
        'categories': {
            'url': reverse('categories-list', request=request, format=format),
            'description': 'Hierarchical categories for organizing linguistic data'
        },
        'phrases': {
            'url': reverse('phrases-all-list', request=request, format=format),
            'description': 'Linguistic phrases linked to samples and research data'
        },
        'samples': {
            'url': reverse('samples-list', request=request, format=format),
            'description': 'Linguistic samples with metadata and associated phrases'
        },
        'answers': {
            'url': reverse('answers-list', request=request, format=format),
            'description': 'Research question answers and analysis data'
        },
        'views': {
            'url': reverse('views-list', request=request, format=format),
            'description': 'HTML template views for data visualization'
        },
        'users': {
            'url': reverse('user-list', request=request, format=format),
            'description': 'User management and authentication'
        },
        'authentication': {
            'token': reverse('api_token_auth', request=request, format=format),
            'description': 'Obtain authentication token with username/password'
        }
    })

urlpatterns = [
    path('', include(router.urls)),
    path("admin/", admin.site.urls),
    path("api-auth/", include("rest_framework.urls", namespace="rest_framework")),
    path("api/token/", obtain_auth_token, name="api_token_auth"),
]
