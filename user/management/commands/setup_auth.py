"""
Automated setup for the custom user model.

Drops and recreates the rms database, runs migrations, and re-seeds
the 4 known users with their project roles.

Usage:
    python manage.py setup_auth
    python manage.py setup_auth --default-password changeme123
"""

import subprocess
import sys

from django.conf import settings
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
    help = "Reset rms database, run migrations, and seed users with project roles."

    def add_arguments(self, parser):
        parser.add_argument(
            "--default-password",
            default="changeme123",
            help="Default password for all seeded users (default: changeme123)",
        )

    def handle(self, *args, **options):
        default_password = options["default_password"]
        db_conf = settings.DATABASES["default"]
        db_name = db_conf["NAME"]
        db_user = db_conf.get("USER", "root")
        db_password = db_conf.get("PASSWORD", "")
        db_host = db_conf.get("HOST", "localhost")
        db_port = db_conf.get("PORT", "3306")

        self.stdout.write(f"\n1. Dropping and recreating database '{db_name}'...")
        mysql_args = ["mysql", f"-u{db_user}", f"-h{db_host}", f"-P{db_port}"]
        if db_password:
            mysql_args.append(f"-p{db_password}")
        subprocess.run(
            mysql_args + ["-e", f"DROP DATABASE IF EXISTS `{db_name}`; CREATE DATABASE `{db_name}`;"],
            check=True,
        )
        self.stdout.write(self.style.SUCCESS(f"   Database '{db_name}' recreated."))

        self.stdout.write("\n2. Running migrations...")
        call_command("migrate", verbosity=0)
        self.stdout.write(self.style.SUCCESS("   Migrations complete."))

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
