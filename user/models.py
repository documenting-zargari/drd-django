from django.contrib.auth.models import AbstractUser
from django.db import models


class CustomUser(AbstractUser):
    is_global_admin = models.BooleanField(
        default=False,
        help_text="Admin for all projects. Overrides per-project roles.",
    )

    def get_role_for_project(self, project):
        if self.is_global_admin:
            return "admin"
        try:
            return self.project_roles.get(project=project).role
        except UserProjectRole.DoesNotExist:
            return None

    def get_allowed_samples_for_project(self, project):
        if self.is_global_admin:
            return []  # empty = unrestricted
        try:
            role = self.project_roles.get(project=project)
            return role.sample_list
        except UserProjectRole.DoesNotExist:
            return []


class UserProjectRole(models.Model):
    """
    Represents the role of a user within a specific project, including their permissions and allowed samples.

    Attributes:
        user (ForeignKey): Reference to the CustomUser assigned to this role.
        project (CharField): The name or identifier of the project.
        role (CharField): The user's role in the project (viewer, editor, admin).
        allowed_samples (TextField): Comma-separated list of sample references the user is allowed to access. Empty means access to all samples.

    Meta:
        unique_together: Ensures a user can have only one role per project.

    Properties:
        sample_list (list): Returns a list of allowed sample references, parsed from allowed_samples.
    """
    ROLE_CHOICES = [
        ("viewer", "Viewer"),
        ("editor", "Editor"),
        ("admin", "Admin"),
    ]
    user = models.ForeignKey(
        CustomUser, on_delete=models.CASCADE, related_name="project_roles"
    )
    project = models.CharField(max_length=50)
    role = models.CharField(max_length=10, choices=ROLE_CHOICES, default="viewer")
    allowed_samples = models.TextField(
        blank=True,
        default="",
        help_text="Comma-separated sample_refs. Empty = all samples in project.",
    )

    class Meta:
        unique_together = ("user", "project")

    def __str__(self):
        return f"{self.user.username} - {self.project} ({self.role})"

    @property
    def sample_list(self):
        if not self.allowed_samples:
            return []
        return [s.strip() for s in self.allowed_samples.split(",") if s.strip()]
