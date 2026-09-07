"""
Idempotent user seeding — safe to run on every container start.
Only creates users that don't already exist. Skips silently if
users are already present.

If the database isn't ready yet (migrations not applied, tables
missing, DB unreachable) the command prints a hint and exits 0
instead of crashing, so it's harmless in a container start script.

Usage:
    python manage.py seed_users
    python manage.py seed_users --default-password changeme123
"""

from django.core.management.base import BaseCommand
from django.db import DatabaseError, connection
from django.db.migrations.executor import MigrationExecutor

from user.models import CustomUser, UserProjectRole
from user.seed_data import SEED_USERS


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

    def _database_not_ready(self):
        """Return a reason string if the DB can't be seeded yet, else None."""
        try:
            executor = MigrationExecutor(connection)
            targets = executor.loader.graph.leaf_nodes()
            if executor.migration_plan(targets):
                return "there are unapplied migrations"
        except DatabaseError as exc:
            return f"the database is not reachable ({exc})"

        if CustomUser._meta.db_table not in connection.introspection.table_names():
            return "the user tables do not exist yet"
        return None

    def handle(self, *args, **options):
        not_ready = self._database_not_ready()
        if not_ready:
            self.stdout.write(
                self.style.WARNING(
                    f"Skipping user seeding: {not_ready}.\n"
                    "\nRun the database setup first, then seed:\n"
                    "  python manage.py migrate\n"
                    "  python manage.py seed_users\n"
                    "\nOr, to migrate + flush all data + recreate the default users\n"
                    "in one step:\n"
                    "  python manage.py setup_auth\n"
                )
            )
            return

        default_password = options["default_password"]
        created_count = 0

        for u in SEED_USERS:
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
