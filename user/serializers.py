from rest_framework import serializers

from user.models import CustomUser, UserProjectRole


class UserProjectRoleSerializer(serializers.ModelSerializer):
    allowed_samples = serializers.SerializerMethodField()

    class Meta:
        model = UserProjectRole
        fields = ["project", "role", "allowed_samples"]

    def get_allowed_samples(self, obj):
        return obj.sample_list


class UserSerializer(serializers.ModelSerializer):
    name = serializers.SerializerMethodField()
    project_roles = UserProjectRoleSerializer(many=True, read_only=True)

    class Meta:
        model = CustomUser
        fields = [
            "id",
            "username",
            "email",
            "first_name",
            "last_name",
            "name",
            "is_global_admin",
            "project_roles",
        ]

    def get_name(self, obj):
        return f"{obj.first_name} {obj.last_name}".strip()


class UserProjectRoleWriteSerializer(serializers.Serializer):
    project = serializers.CharField(max_length=50)
    role = serializers.ChoiceField(choices=UserProjectRole.ROLE_CHOICES)
    allowed_samples = serializers.ListField(
        child=serializers.CharField(), required=False, default=list
    )


class UserWriteSerializer(serializers.ModelSerializer):
    password = serializers.CharField(write_only=True, required=False, min_length=6)
    project_roles = UserProjectRoleWriteSerializer(many=True, required=False)

    class Meta:
        model = CustomUser
        fields = [
            "id",
            "username",
            "email",
            "first_name",
            "last_name",
            "is_global_admin",
            "password",
            "project_roles",
        ]
        read_only_fields = ["id"]

    def create(self, validated_data):
        roles_data = validated_data.pop("project_roles", [])
        password = validated_data.pop("password", None)
        user = CustomUser(**validated_data)
        if password:
            user.set_password(password)
        user.save()
        self._sync_roles(user, roles_data)
        return user

    def update(self, instance, validated_data):
        roles_data = validated_data.pop("project_roles", None)
        password = validated_data.pop("password", None)
        for attr, value in validated_data.items():
            setattr(instance, attr, value)
        if password:
            instance.set_password(password)
        instance.save()
        if roles_data is not None:
            self._sync_roles(instance, roles_data)
        return instance

    def _sync_roles(self, user, roles_data):
        # admin_projects is None for global admins (unrestricted),
        # or a list of project names for project admins.
        admin_projects = self.context.get("admin_projects")
        if admin_projects is None:
            # Global admin: replace all roles
            user.project_roles.all().delete()
        else:
            # Project admin: only delete roles for their projects, keep the rest
            user.project_roles.filter(project__in=admin_projects).delete()
        for role_data in roles_data:
            samples = role_data.get("allowed_samples", [])
            UserProjectRole.objects.create(
                user=user,
                project=role_data["project"],
                role=role_data["role"],
                allowed_samples=",".join(samples),
            )

    def to_representation(self, instance):
        return UserSerializer(instance).data
