"""
Write the encrypted user export (accounts, project roles, groups) to a file.

Usage:
    python manage.py export_users rld-users.json.enc
"""

from django.core.management.base import BaseCommand

from user.export import encrypt, fingerprint, users_fixture


class Command(BaseCommand):
    help = "Export users and their roles, encrypted with RMS_ENCRYPT_KEY."

    def add_arguments(self, parser):
        parser.add_argument("path", help="output file")

    def handle(self, *args, **options):
        plaintext = users_fixture()
        with open(options["path"], "wb") as fh:
            fh.write(encrypt(plaintext))
        self.stdout.write(self.style.SUCCESS(
            f"Wrote {options['path']} (fingerprint {fingerprint(plaintext)})"
        ))
