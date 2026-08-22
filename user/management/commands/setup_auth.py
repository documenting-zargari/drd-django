"""
Automated setup for the custom user model.

Runs migrations, flushes all data (keeping the schema), and re-seeds
the 4 known users with their project roles.

Note: this does NOT drop/recreate the database itself. Managed Postgres
instances typically grant the app role ownership of its one database but
not CREATEDB, so DROP DATABASE/CREATE DATABASE would fail there (and if
the DROP succeeds before the CREATE fails, you're left with no database
at all). `flush` clears all rows without touching the database object.

Usage:
    python manage.py setup_auth
    python manage.py setup_auth --default-password changeme123
"""

from django.core.management import call_command
from django.core.management.base import BaseCommand


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
    help = "Run migrations, flush all data, and seed users with project roles."

    def add_arguments(self, parser):
        parser.add_argument(
            "--default-password",
            default="changeme123",
            help="Default password for all seeded users (default: changeme123)",
        )

    def handle(self, *args, **options):
        default_password = options["default_password"]

        self.stdout.write("\n1. Running migrations...")
        call_command("migrate", verbosity=0)
        self.stdout.write(self.style.SUCCESS("   Migrations complete."))

        self.stdout.write("\n2. Flushing all data...")
        call_command("flush", interactive=False, verbosity=0)
        self.stdout.write(self.style.SUCCESS("   Database flushed."))

        self.stdout.write(f"\n3. Seeding {len(USERS)} users (password: {default_password})...")
        from user.models import CustomUser, UserProjectRole

        for u in USERS:
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
            roles_str = ", ".join(
                f'{r["project"]}:{r["role"]}' for r in u["project_roles"]
            )
            admin_str = " [GLOBAL ADMIN]" if u["is_global_admin"] else ""
            self.stdout.write(f"   {user.username} ({roles_str}){admin_str}")

        self.stdout.write(self.style.SUCCESS("\nDone. All users created."))
        self.stdout.write(
            f"\nAll passwords set to: {default_password}"
            "\nChange them via Django admin or 'python manage.py changepassword <username>'"
        )
