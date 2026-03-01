from rest_framework import status, viewsets
from rest_framework.authtoken.models import Token
from rest_framework.decorators import action, api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response
from rest_framework.views import APIView

from user.models import CustomUser
from user.permissions import IsGlobalAdmin, IsAdminOrSelf, IsGlobalOrProjectAdmin, get_project_from_request
from user.serializers import UserSerializer, UserWriteSerializer


class CustomObtainAuthToken(APIView):
    """Login endpoint that returns token + user info with project roles."""

    permission_classes = []

    def post(self, request):
        username = request.data.get("username")
        password = request.data.get("password")
        if not username or not password:
            return Response(
                {"error": "Username and password required."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        try:
            user = CustomUser.objects.get(username=username)
        except CustomUser.DoesNotExist:
            return Response(
                {"error": "Invalid credentials."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if not user.check_password(password):
            return Response(
                {"error": "Invalid credentials."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        token, _ = Token.objects.get_or_create(user=user)
        serializer = UserSerializer(user)
        return Response({"token": token.key, **serializer.data})


@api_view(["POST"])
@permission_classes([IsAuthenticated])
def logout_view(request):
    """Delete the user's auth token."""
    request.user.auth_token.delete()
    return Response(status=status.HTTP_200_OK)


def _get_admin_projects(user):
    """Return list of project names where user is admin (empty for global admins)."""
    if user.is_global_admin:
        return None  # None = unrestricted
    return list(
        user.project_roles.filter(role="admin").values_list("project", flat=True)
    )


class UserViewSet(viewsets.ModelViewSet):
    queryset = CustomUser.objects.all().order_by("pk")
    serializer_class = UserSerializer

    def get_serializer_class(self):
        if self.action in ("create", "update", "partial_update"):
            return UserWriteSerializer
        return UserSerializer

    def get_permissions(self):
        if self.action in ("me", "change_password"):
            return [IsAuthenticated()]
        if self.action in ("list", "retrieve"):
            return [IsAuthenticated()]
        if self.action == "destroy":
            return [IsGlobalAdmin()]
        if self.action == "create":
            return [IsGlobalOrProjectAdmin()]
        # update, partial_update
        return [IsAdminOrSelf()]

    def _sanitize_for_project_admin(self, request):
        """Strip fields that only global admins may set."""
        data = request.data
        if hasattr(data, '_mutable'):
            data._mutable = True
        # Project admins cannot set is_global_admin
        if "is_global_admin" in data:
            data["is_global_admin"] = False
        # Scope project_roles to the admin's projects
        admin_projects = _get_admin_projects(request.user)
        if admin_projects is not None and "project_roles" in data:
            roles = data["project_roles"]
            for role in roles:
                if role.get("project") not in admin_projects:
                    return Response(
                        {"error": f"You do not have admin rights for project '{role.get('project')}'."},
                        status=status.HTTP_403_FORBIDDEN,
                    )
        return None  # no error

    def create(self, request, *args, **kwargs):
        if not request.user.is_global_admin:
            err = self._sanitize_for_project_admin(request)
            if err:
                return err
        return super().create(request, *args, **kwargs)

    def update(self, request, *args, **kwargs):
        instance = self.get_object()
        # Project admins cannot edit global admins
        if not request.user.is_global_admin and instance.is_global_admin:
            return Response(
                {"error": "You cannot edit a global admin."},
                status=status.HTTP_403_FORBIDDEN,
            )
        if not request.user.is_global_admin:
            err = self._sanitize_for_project_admin(request)
            if err:
                return err
        return super().update(request, *args, **kwargs)

    def partial_update(self, request, *args, **kwargs):
        instance = self.get_object()
        if not request.user.is_global_admin and instance.is_global_admin:
            return Response(
                {"error": "You cannot edit a global admin."},
                status=status.HTTP_403_FORBIDDEN,
            )
        if not request.user.is_global_admin:
            err = self._sanitize_for_project_admin(request)
            if err:
                return err
        return super().partial_update(request, *args, **kwargs)

    def get_serializer_context(self):
        ctx = super().get_serializer_context()
        ctx["admin_projects"] = _get_admin_projects(self.request.user)
        return ctx

    @action(detail=False, methods=["get"], url_path="me")
    def me(self, request):
        serializer = UserSerializer(request.user)
        return Response(serializer.data)

    @action(detail=False, methods=["post"], url_path="me/change-password")
    def change_password(self, request):
        user = request.user
        old_password = request.data.get("old_password")
        new_password = request.data.get("new_password")
        if not old_password or not new_password:
            return Response(
                {"error": "old_password and new_password are required."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if not user.check_password(old_password):
            return Response(
                {"error": "Current password is incorrect."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        if len(new_password) < 6:
            return Response(
                {"error": "New password must be at least 6 characters."},
                status=status.HTTP_400_BAD_REQUEST,
            )
        user.set_password(new_password)
        user.save()
        # Regenerate token so the user stays logged in
        Token.objects.filter(user=user).delete()
        new_token = Token.objects.create(user=user)
        return Response({"token": new_token.key, "message": "Password changed successfully."})

    def list(self, request, *args, **kwargs):
        user = request.user
        if user.is_global_admin:
            return super().list(request, *args, **kwargs)
        # Project admin sees users in their project
        project = get_project_from_request(request)
        role = user.get_role_for_project(project)
        if role == "admin":
            project_user_ids = (
                CustomUser.objects.filter(project_roles__project=project)
                .values_list("id", flat=True)
            )
            self.queryset = CustomUser.objects.filter(id__in=project_user_ids).order_by("pk")
            return super().list(request, *args, **kwargs)
        # Non-admin authenticated users only see themselves
        serializer = self.get_serializer(user)
        return Response([serializer.data])
