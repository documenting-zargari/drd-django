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
from rest_framework.decorators import api_view
from rest_framework.response import Response
from rest_framework.reverse import reverse

import data.urls
import user.urls
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

    Every link below is clickable in this browsable interface — follow one
    to see that endpoint's own docstring (query parameters, request/response
    shape, etc.) and, for list endpoints, a further "GET"/"OPTIONS" form to
    try it directly.
    """
    return Response(
        {
            "categories": {
                "url": reverse("categories-list", request=request, format=format),
                "description": "Hierarchical categories/research-question tree (GET ?parent_id=<id> to list children)",
                "search": reverse("categories-search", request=request, format=format),
                "batch": reverse("categories-batch", request=request, format=format),
                "search_views": reverse("categories-search-views", request=request, format=format),
            },
            "research-questions": {
                "url": reverse("research-questions-list", request=request, format=format),
                "description": "Leaf ResearchQuestions (a subset of the Categories tree, where is_leaf=true)",
                "search": reverse("research-questions-search", request=request, format=format),
                "batch": reverse("research-questions-batch", request=request, format=format),
            },
            "phrases": {
                "url": reverse("phrases-list", request=request, format=format),
                "description": "Linguistic phrases linked to samples and research data. GET ?sample=<ref> for one "
                               "sample's phrases; PATCH a detail url to edit phrase text/question_overrides.",
                "phrase_list": reverse("phrases-phrase-list", request=request, format=format),
                "search": reverse("phrases-search", request=request, format=format),
                "export": reverse("phrases-export", request=request, format=format),
                "by_answer": reverse("phrases-by-answer", request=request, format=format),
                "by_category": reverse("phrases-by-category", request=request, format=format),
            },
            "master-phrases": {
                "url": reverse("master-phrases-list", request=request, format=format),
                "description": "Phrase-concept-level fields (english, conjugated, question_ids, category_ids) shared "
                               "across every sample's recording of a phrase_ref. Global-admin only; PATCH a detail url.",
            },
            "transcriptions": {
                "url": reverse("transcriptions-list", request=request, format=format),
                "description": "Connected-speech transcriptions linked to samples and research data. "
                               "GET ?sample=<ref> for one sample's transcriptions.",
                "search": reverse("transcriptions-search", request=request, format=format),
                "export": reverse("transcriptions-export", request=request, format=format),
                "by_answer": reverse("transcriptions-by-answer", request=request, format=format),
                "by_category": reverse("transcriptions-by-category", request=request, format=format),
            },
            "related": {
                "url": reverse("related-list", request=request, format=format),
                "description": "Combined phrases + transcriptions for a research question/category id and sample "
                               "in one request (preferred over calling phrases.by_category and "
                               "transcriptions.by_category separately) — GET ?category_id=&sample=&answer_key=",
            },
            "samples": {
                "url": reverse("samples-list", request=request, format=format),
                "description": "Linguistic samples with metadata and associated phrases",
                "with_transcriptions": reverse("samples-with-transcriptions", request=request, format=format),
                "check": reverse("samples-check-sample-ref", request=request, format=format),
                "import_template": reverse("samples-import-template", request=request, format=format),
                "import": reverse("samples-import-sample", request=request, format=format),
                "import_history": reverse("samples-import-history", request=request, format=format),
            },
            "answers": {
                "url": reverse("answers-list", request=request, format=format),
                "description": "Research question answers and analysis data. PATCH a detail url to edit; "
                               "PUT create/ to create a new answer.",
                "create": reverse("answers-create-answer", request=request, format=format),
            },
            "views": {
                "url": reverse("views-list", request=request, format=format),
                "description": "HTML template views (JAML) for data visualization",
            },
            "backups": {
                "url": reverse("backups-list", request=request, format=format),
                "description": "Database backup/restore management",
            },
            "users": {
                "url": reverse("customuser-list", request=request, format=format),
                "description": "User management and authentication",
            },
            "authentication": {
                "token": reverse("api_token_auth", request=request, format=format),
                "logout": reverse("api_logout", request=request, format=format),
                "description": "Obtain authentication token with username/password",
            },
        }
    )


urlpatterns = [
    # Custom root with per-endpoint descriptions, shadowing the router's own
    # (undescribed) root view — must come before include(router.urls) since
    # Django resolves urlpatterns in order and both match "".
    path("", api_root, name="api-root"),
    path("", include(router.urls)),
    path("admin/", admin.site.urls),
    path("api-auth/", include("rest_framework.urls", namespace="rest_framework")),
    path("api/token/", CustomObtainAuthToken.as_view(), name="api_token_auth"),
    path("api/logout/", logout_view, name="api_logout"),
]
