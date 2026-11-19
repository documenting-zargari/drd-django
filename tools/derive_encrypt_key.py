#!/usr/bin/env python3
"""
Derive RMS_ENCRYPT_KEY, the key the backend uses to decrypt personal data
fields stored with an "ENC:" prefix (speaker name/age/occupation/comments,
fieldworker, transcriber, ... in the Sources collection).

The key is never stored or published. It is derived from two inputs:
    - the master password (prompted, never echoed), and
    - the salt (64 hex characters; ENCRYPT_SALT),
with exactly the parameters the data was encrypted with
(extract/encrypt_personal_data.py): PBKDF2-HMAC-SHA256, 480,000 iterations,
32-byte key, urlsafe base64 -> a Fernet key.

Both inputs are needed; either one alone is useless. Use --check with an
unpacked arangodump to confirm the derived key really decrypts the data
before putting it into the backend's environment.

Requires: pip install cryptography   (already in requirements.txt)

Examples:
    python3 derive_encrypt_key.py --salt <64 hex chars>
    ENCRYPT_SALT=<hex> python3 derive_encrypt_key.py --check ./dump-multiple
    → prints  RMS_ENCRYPT_KEY=...   (put that line into the backend's .env)
"""

import argparse
import base64
import getpass
import os
import sys

from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

ENC_PREFIX = "ENC:"
# Must match extract/encrypt_personal_data.py, or the derived key won't decrypt.
ITERATIONS = 480_000


def derive_key(password: str, salt: bytes) -> bytes:
    kdf = PBKDF2HMAC(algorithm=hashes.SHA256(), length=32, salt=salt, iterations=ITERATIONS)
    return base64.urlsafe_b64encode(kdf.derive(password.encode("utf-8")))


def check_against_dump(key: bytes, dump_dir: str) -> bool:
    """Try the key on every ENC: value in the dump's Sources; report how many decrypt."""
    from download_audio import (
        dump_records,  # sibling script; reads plain or gzipped dump files
    )

    fernet = Fernet(key)
    ok = bad = 0
    for doc in dump_records(dump_dir, "Sources"):
        for value in doc.values():
            if isinstance(value, str) and value.startswith(ENC_PREFIX):
                try:
                    fernet.decrypt(value[len(ENC_PREFIX):].encode())
                    ok += 1
                except InvalidToken:
                    bad += 1
    print(f"Check: {ok} encrypted values decrypt, {bad} do not.", file=sys.stderr)
    if ok + bad == 0:
        print("Check: no ENC: values found in Sources — nothing to verify.", file=sys.stderr)
    return bad == 0 and ok > 0


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--salt", default=os.environ.get("ENCRYPT_SALT"),
                    help="salt as 64 hex characters (default: $ENCRYPT_SALT)")
    ap.add_argument("--check", metavar="DUMP_DIR", help="verify the key against an unpacked arangodump")
    args = ap.parse_args()

    if not args.salt:
        sys.exit("No salt: pass --salt or set ENCRYPT_SALT.")
    try:
        salt = bytes.fromhex(args.salt.strip())
    except ValueError:
        sys.exit("The salt must be hex characters only.")
    if len(salt) != 32:
        sys.exit(f"The salt must be 64 hex characters (32 bytes); got {len(salt) * 2}.")

    password = getpass.getpass("Master password: ")
    if not password:
        sys.exit("A password is required.")

    key = derive_key(password, salt)
    if args.check and not check_against_dump(key, args.check):
        sys.exit("The key does not decrypt the data: wrong password or salt.")
    print(f"RMS_ENCRYPT_KEY={key.decode()}")


if __name__ == "__main__":
    main()
