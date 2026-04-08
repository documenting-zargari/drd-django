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
from rest_framework import routers
from rest_framework.decorators import api_view, permission_classes
from rest_framework.response import Response
from rest_framework.reverse import reverse

import data.urls
import user.urls
from user.permissions import IsGlobalOrProjectAdmin
from user.views import CustomObtainAuthToken, logout_view

router = routers.DefaultRouter()
router.registry.extend(data.urls.router.registry)
router.registry.extend(user.urls.router.registry)


@api_view(["GET"])
def api_root(request, format=None):
    """
    Roma API Root

    This API provides access to linguistic data and research materials.
    Authentication is required for most endpoints using Token Authentication.
    """
    return Response(
        {
            "categories": {
                "url": reverse("categories-list", request=request, format=format),
                "description": "Hierarchical categories for organizing linguistic data",
            },
            "phrases": {
                "url": reverse("phrases-all-list", request=request, format=format),
                "description": "Linguistic phrases linked to samples and research data",
            },
            "samples": {
                "url": reverse("samples-list", request=request, format=format),
                "description": "Linguistic samples with metadata and associated phrases",
            },
            "answers": {
                "url": reverse("answers-list", request=request, format=format),
                "description": "Research question answers and analysis data",
            },
            "views": {
                "url": reverse("views-list", request=request, format=format),
                "description": "HTML template views for data visualization",
            },
            "users": {
                "url": reverse("user-list", request=request, format=format),
                "description": "User management and authentication",
            },
            "authentication": {
                "token": reverse("api_token_auth", request=request, format=format),
                "logout": reverse("api_logout", request=request, format=format),
                "description": "Obtain authentication token with username/password",
            },
        }
    )


def _arango_backup_request(method, path, body=None):
    """Make a request to the ArangoDB backup API."""
    import requests
    from django.conf import settings
    url = f"{settings.ARANGO_HOST}/_admin/backup/{path}"
    auth = (settings.ARANGO_USERNAME.strip(), settings.ARANGO_PASSWORD)
    if method == "GET":
        return requests.get(url, auth=auth, timeout=30)
    return requests.post(url, auth=auth, json=body or {}, timeout=60)


@api_view(["POST"])
@permission_classes([IsGlobalOrProjectAdmin])
def backup_create(request):
    """Create a hot backup. Body: {"label": "optional-label"}"""
    label = request.data.get("label", "manual")
    resp = _arango_backup_request("POST", "create", {"label": label, "timeout": 30})
    if resp.status_code in (200, 201):
        return Response(resp.json().get("result", resp.json()))
    return Response(resp.json(), status=resp.status_code)


@api_view(["GET"])
@permission_classes([IsGlobalOrProjectAdmin])
def backup_list(request):
    """List all available hot backups."""
    resp = _arango_backup_request("POST", "list")
    if resp.status_code == 200:
        result = resp.json().get("result", {})
        backups = list(result.get("list", {}).values())
        backups.sort(key=lambda b: b.get("datetime", ""), reverse=True)
        return Response(backups)
    return Response(resp.json(), status=resp.status_code)


@api_view(["POST"])
@permission_classes([IsGlobalOrProjectAdmin])
def backup_restore(request):
    """Restore a backup. Body: {"id": "backup-id"}"""
    backup_id = request.data.get("id")
    if not backup_id:
        return Response({"error": "id is required"}, status=400)
    resp = _arango_backup_request("POST", "restore", {"id": backup_id})
    if resp.status_code == 200:
        return Response(resp.json().get("result", resp.json()))
    return Response(resp.json(), status=resp.status_code)


@api_view(["POST"])
@permission_classes([IsGlobalOrProjectAdmin])
def backup_delete(request):
    """Delete a backup. Body: {"id": "backup-id"}"""
    backup_id = request.data.get("id")
    if not backup_id:
        return Response({"error": "id is required"}, status=400)
    resp = _arango_backup_request("POST", "delete", {"id": backup_id})
    if resp.status_code == 200:
        return Response({"deleted": backup_id})
    return Response(resp.json(), status=resp.status_code)


urlpatterns = [
    path("", include(router.urls)),
    path("admin/", admin.site.urls),
    path("api-auth/", include("rest_framework.urls", namespace="rest_framework")),
    path("api/token/", CustomObtainAuthToken.as_view(), name="api_token_auth"),
    path("api/logout/", logout_view, name="api_logout"),
    path("backup/create/", backup_create, name="backup_create"),
    path("backup/list/", backup_list, name="backup_list"),
    path("backup/restore/", backup_restore, name="backup_restore"),
    path("backup/delete/", backup_delete, name="backup_delete"),
]
