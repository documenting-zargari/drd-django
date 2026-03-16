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


class CanEditSample(BasePermission):
    """
    Write permission for sample-scoped data.
    Safe methods are always allowed (public read access).
    Write methods require editor+ role for the project, and if the user
    has sample restrictions, the target sample must be in their allowed list.

    Views using this permission must provide a `get_sample_ref(request)` method
    that extracts the target sample_ref from the request.
    """

    def has_permission(self, request, view):
        if request.method in SAFE_METHODS:
            return True
        if not request.user or not request.user.is_authenticated:
            return False
        project = get_project_from_request(request)
        # Must be at least editor
        role = request.user.get_role_for_project(project)
        if role not in ("editor", "admin"):
            return False
        # Admins can edit any sample
        if role == "admin" or request.user.is_global_admin:
            return True
        # Editor with possible sample restrictions
        allowed = request.user.get_allowed_samples_for_project(project)
        if not allowed:
            return True  # empty = unrestricted
        sample_ref = getattr(view, "get_sample_ref", lambda r: None)(request)
        if sample_ref is None:
            return False  # can't determine sample — deny
        return sample_ref in allowed


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
