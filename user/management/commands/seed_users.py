"""
Idempotent user seeding — safe to run on every container start.
Only creates users that don't already exist. Skips silently if
users are already present.

Usage:
    python manage.py seed_users
    python manage.py seed_users --default-password changeme123
"""

from django.core.management.base import BaseCommand

from user.models import CustomUser, UserProjectRole


USERS = [
    {
        "username": "mundstein",
        "email": "smundstein@gmail.com",
        "first_name": "Sascha",
        "last_name": "Mundstein",
        "is_global_admin": True,
        "project_roles": [{"project": "rms", "role": "admin"}],
    },
    {
        "username": "wiedner",
        "email": "jakob.wiedner@uni-graz.ac.at",
        "first_name": "Jakob",
        "last_name": "Wiedner",
        "is_global_admin": False,
        "project_roles": [{"project": "rms", "role": "editor"}],
    },
    {
        "username": "aminian",
        "email": "Ioana.Aminian@oeaw.ac.at",
        "first_name": "Ioana",
        "last_name": "Aminian-Jazi",
        "is_global_admin": False,
        "project_roles": [{"project": "rms", "role": "editor"}],
    },
    {
        "username": "yaron",
        "email": "y.matras@aston.ac.uk",
        "first_name": "Yaron",
        "last_name": "Matras",
        "is_global_admin": False,
        "project_roles": [{"project": "rms", "role": "admin"}],
    },
]


class Command(BaseCommand):
    help = "Seed default users if they don't exist. Safe to run repeatedly."

    def add_arguments(self, parser):
        parser.add_argument(
            "--default-password",
            default="changeme123",
            help="Default password for newly created users (default: changeme123)",
        )
        parser.add_argument(
            "--no-input",
            action="store_true",
            help="Run without prompts.",
        )

    def handle(self, *args, **options):
        default_password = options["default_password"]
        created_count = 0

        for u in USERS:
            if CustomUser.objects.filter(username=u["username"]).exists():
                continue

            user = CustomUser.objects.create_user(
                username=u["username"],
                email=u["email"],
                password=default_password,
                first_name=u["first_name"],
                last_name=u["last_name"],
                is_global_admin=u["is_global_admin"],
                is_staff=u["is_global_admin"],
            )
            for role_data in u["project_roles"]:
                UserProjectRole.objects.create(
                    user=user,
                    project=role_data["project"],
                    role=role_data["role"],
                )
            created_count += 1
            self.stdout.write(f"  Created user: {user.username}")

        if created_count == 0:
            self.stdout.write("  Users already exist, nothing to seed.")
        else:
            self.stdout.write(
                self.style.SUCCESS(f"  Seeded {created_count} user(s).")
            )
