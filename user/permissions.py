from rest_framework.permissions import BasePermission, SAFE_METHODS


def get_project_from_request(request):
    """Extract project identifier from request. Defaults to 'rms'."""
    return request.headers.get("X-Project", "rms")


class ReadOnlyOrAuthenticated(BasePermission):
    """
    Global default: safe methods (GET/HEAD/OPTIONS) always allowed,
    write methods require authentication.
    """

    def has_permission(self, request, view):
        if request.method in SAFE_METHODS:
            return True
        return request.user and request.user.is_authenticated


class IsProjectEditor(BasePermission):
    """Requires editor or admin role for the project."""

    def has_permission(self, request, view):
        if not request.user or not request.user.is_authenticated:
            return False
        project = get_project_from_request(request)
        role = request.user.get_role_for_project(project)
        return role in ("editor", "admin")


class IsProjectAdmin(BasePermission):
    """Requires admin role for the project."""

    def has_permission(self, request, view):
        if not request.user or not request.user.is_authenticated:
            return False
        project = get_project_from_request(request)
        role = request.user.get_role_for_project(project)
        return role == "admin"


class IsGlobalAdmin(BasePermission):
    """Requires is_global_admin flag on user."""

    def has_permission(self, request, view):
        return (
            request.user
            and request.user.is_authenticated
            and request.user.is_global_admin
        )


class IsGlobalOrProjectAdmin(BasePermission):
    """Requires global admin or project admin role."""

    def has_permission(self, request, view):
        if not request.user or not request.user.is_authenticated:
            return False
        if request.user.is_global_admin:
            return True
        project = get_project_from_request(request)
        return request.user.get_role_for_project(project) == "admin"


class IsAdminOrSelf(BasePermission):
    """
    Object-level: allows global admin, project admin, or the user themselves.
    """

    def has_object_permission(self, request, view, obj):
        if not request.user or not request.user.is_authenticated:
            return False
        # Own record
        if obj == request.user:
            return True
        # Global admin
        if request.user.is_global_admin:
            return True
        # Project admin for any shared project
        project = get_project_from_request(request)
        return request.user.get_role_for_project(project) == "admin"
