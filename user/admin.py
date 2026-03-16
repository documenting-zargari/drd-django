from django.contrib import admin
from django.contrib.auth.admin import UserAdmin

from user.models import CustomUser, UserProjectRole


class UserProjectRoleInline(admin.TabularInline):
    model = UserProjectRole
    extra = 1


@admin.register(CustomUser)
class CustomUserAdmin(UserAdmin):
    inlines = [UserProjectRoleInline]
    list_display = ("username", "email", "first_name", "last_name", "is_global_admin")
    fieldsets = UserAdmin.fieldsets + (
        ("Project Access", {"fields": ("is_global_admin",)}),
    )


@admin.register(UserProjectRole)
class UserProjectRoleAdmin(admin.ModelAdmin):
    list_display = ("user", "project", "role", "allowed_samples")
    list_filter = ("project", "role")
