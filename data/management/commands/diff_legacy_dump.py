"""
Diff two arangodump export directories, key-by-key, per collection.

Background
----------
Production kept running the pre-refactor schema after the local
MasterPhrases/SamplePhrases migration baseline was cut, and real users kept
editing it there. This command finds exactly what changed between that
baseline export and a later production export, without knowing anything
about what the changes *mean* — it just reports added/removed/changed _keys
per collection. `replay_legacy_changes` (a separate command) is what
understands how to translate those changes into the new schema.

Generic by design: point --baseline/--current at any two arangodump
directories of the same database lineage (same _key space) to get a
changeset. Re-run any time a new production pull needs reconciling.

Usage:
    python manage.py diff_legacy_dump \\
        --baseline /path/to/snapshot_20260713_1900 \\
        --current /path/to/rancher/dump-multiple/rms \\
        --output changeset.json
"""

import json
from datetime import datetime, timezone
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from data.legacy_replay import discover_collections, load_dump_collection, normalize_doc


class Command(BaseCommand):
    help = "Diff two arangodump directories per collection, writing an added/removed/changed changeset."

    def add_arguments(self, parser):
        parser.add_argument("--baseline", required=True, help="Older arangodump directory (the migration cutover point).")
        parser.add_argument("--current", required=True, help="Newer arangodump directory (e.g. a fresh production pull).")
        parser.add_argument("--output", required=True, help="Path to write the changeset JSON to.")
        parser.add_argument(
            "--collections",
            help="Comma-separated collection names to diff. Default: every collection present in either directory.",
        )

    def handle(self, *args, **options):
        baseline_dir = Path(options["baseline"])
        current_dir = Path(options["current"])
        if not baseline_dir.is_dir():
            raise CommandError(f"--baseline {baseline_dir} is not a directory")
        if not current_dir.is_dir():
            raise CommandError(f"--current {current_dir} is not a directory")

        if options["collections"]:
            collections = [c.strip() for c in options["collections"].split(",") if c.strip()]
        else:
            collections = sorted(discover_collections(baseline_dir) | discover_collections(current_dir))

        changeset = {
            "baseline": str(baseline_dir),
            "current": str(current_dir),
            "generated_at": datetime.now(timezone.utc).isoformat(),
            "collections": {},
        }

        self.stdout.write(f"{'collection':<20} {'baseline':>9} {'current':>9} {'added':>7} {'removed':>8} {'changed':>8}")
        for name in collections:
            baseline_docs = load_dump_collection(baseline_dir, name)
            current_docs = load_dump_collection(current_dir, name)
            if baseline_docs is None and current_docs is None:
                continue
            baseline_docs = baseline_docs or {}
            current_docs = current_docs or {}

            added = sorted(set(current_docs) - set(baseline_docs))
            removed = sorted(set(baseline_docs) - set(current_docs))
            changed = sorted(
                k for k in (set(baseline_docs) & set(current_docs))
                if normalize_doc(baseline_docs[k]) != normalize_doc(current_docs[k])
            )
            changeset["collections"][name] = {"added": added, "removed": removed, "changed": changed}

            self.stdout.write(
                f"{name:<20} {len(baseline_docs):>9} {len(current_docs):>9} "
                f"{len(added):>7} {len(removed):>8} {len(changed):>8}"
            )

        output_path = Path(options["output"])
        output_path.write_text(json.dumps(changeset, indent=2))
        self.stdout.write(self.style.SUCCESS(f"\nChangeset written to {output_path}"))
