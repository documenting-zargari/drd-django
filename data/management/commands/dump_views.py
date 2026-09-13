"""
Read-only backup of the live ``Views`` collection to a local JSON file.

This is the Phase-3 prerequisite the user asked for: before the legacy
``content`` HTML field (and the client's legacy parser) are ever deleted, take
a backup that can restore the collection exactly as it stood. Every document
is dumped verbatim - ``filename``, ``slug``, ``content``, ``spec``,
``schema_version``, ``parent_id``, and anything else on the doc - alongside
its live `_key` so a restore can round-trip.

This is a convenience wrapper. The equivalent, and equally valid,
`arangodump` invocation (talks straight to Arango, no Django involved) is:

    arangodump --server.database <ARANGO_DB_NAME> --collection Views \\
        --output-directory <dir>

Usage:
    python manage.py dump_views                         # writes ./views_backup_<timestamp>.json
    python manage.py dump_views --out /path/to/file.json
"""

import json
from datetime import datetime, timezone

from django.core.management.base import BaseCommand

from data.models import View


class Command(BaseCommand):
    help = "Back up every document in the Views collection to a local JSON file."

    def add_arguments(self, parser):
        parser.add_argument(
            "--out",
            help="Output file path. Defaults to ./views_backup_<UTC timestamp>.json",
        )

    def handle(self, *args, **options):
        docs = list(View.collection().all())

        out_path = options["out"]
        if not out_path:
            stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
            out_path = f"views_backup_{stamp}.json"

        payload = {
            "collection": "Views",
            "dumped_at": datetime.now(timezone.utc).isoformat(),
            "count": len(docs),
            "documents": docs,
        }

        with open(out_path, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, indent=2, ensure_ascii=False, default=str)

        self.stdout.write(self.style.SUCCESS(f"Backed up {len(docs)} Views documents -> {out_path}"))
