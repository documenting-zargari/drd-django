"""
Encrypted export/import of the user database (accounts, project roles,
groups), so a restored RLD keeps its users and their access levels.

The export is Django fixture JSON (passwords only as hashes), encrypted with
the same Fernet key as the personal data fields (settings.FIELD_FERNET,
from RMS_ENCRYPT_KEY; see tools/derive_encrypt_key.py). Without that key the
file is unreadable, so it can be published next to the public data dumps.
"""

import hashlib
import io
import json
import os
import tempfile

from django.conf import settings
from django.core.management import call_command

EXPORT_MODELS = ["auth.Group", "user.CustomUser", "user.UserProjectRole"]


def users_fixture() -> bytes:
    """The user tables as fixture JSON. last_login is dropped so the export
    (and its fingerprint) only changes when accounts or roles change."""
    out = io.StringIO()
    call_command("dumpdata", *EXPORT_MODELS, use_natural_foreign_keys=True, indent=1, stdout=out)
    records = json.loads(out.getvalue())
    for rec in records:
        if rec["model"] == "user.customuser":
            rec["fields"].pop("last_login", None)
    return json.dumps(records, indent=1, ensure_ascii=False).encode("utf-8")


def fingerprint(plaintext: bytes) -> str:
    return hashlib.sha256(plaintext).hexdigest()[:16]


def _fernet():
    if settings.FIELD_FERNET is None:
        raise RuntimeError("RMS_ENCRYPT_KEY is not set")
    return settings.FIELD_FERNET


def encrypt(plaintext: bytes) -> bytes:
    return _fernet().encrypt(plaintext)


def import_encrypted(token: bytes) -> int:
    """Decrypt an export and load it. Users with the same id are overwritten.
    Returns the number of records loaded."""
    plaintext = _fernet().decrypt(token)
    with tempfile.NamedTemporaryFile("wb", suffix=".json", delete=False) as fh:
        fh.write(plaintext)
        path = fh.name
    try:
        call_command("loaddata", path, verbosity=0)
    finally:
        os.unlink(path)
    return len(json.loads(plaintext))
