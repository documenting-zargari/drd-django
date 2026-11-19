"""
Load an encrypted user export (from export_users or a published release).
Needs the RMS_ENCRYPT_KEY the export was made with. Users with the same id
are overwritten; others are kept.

Usage:
    python manage.py import_users rld-users.json.enc
"""

from cryptography.fernet import InvalidToken
from django.core.management.base import BaseCommand, CommandError

from user.export import import_encrypted


class Command(BaseCommand):
    help = "Import users and their roles from an encrypted export."

    def add_arguments(self, parser):
        parser.add_argument("path", help="encrypted export file")

    def handle(self, *args, **options):
        with open(options["path"], "rb") as fh:
            token = fh.read()
        try:
            count = import_encrypted(token)
        except InvalidToken:
            raise CommandError("Cannot decrypt: RMS_ENCRYPT_KEY does not match the export.")
        except RuntimeError as e:
            raise CommandError(str(e))
        self.stdout.write(self.style.SUCCESS(f"Imported {count} records."))
