"""
One-off, idempotent cleanup of Sample.country_code.

The legacy RMS database stored ``country_code`` using the same ad-hoc scheme
as the sample_ref prefixes ("EST", "FIN", "RUS", "SLO", "SP", "N", ...) rather
than ISO 3166-1 alpha-2. This command rewrites those to proper alpha-2 codes
and backfills the handful of samples whose ``country_code`` was NULL, using
the sample_ref prefix / location / coordinates as evidence.

Safe to run repeatedly: every write is checked against the target value first,
so a second run is a no-op.

Usage:
    python manage.py normalize_country_codes --dry-run
    python manage.py normalize_country_codes
"""

from django.core.management.base import BaseCommand

from data.country_codes import ISO_REMAP
from data.models import Sample

# "YU" is deliberately kept: the samples span Serbia, Kosovo and Montenegro,
# so no single successor state is correct. "YU" is an exceptionally reserved
# ISO 3166-1 code and is resolved to a label ("Yugoslavia (former)") on the
# client side.
KEEP_AS_IS = {"YU"}

# Samples whose country_code was NULL. Resolved individually from
# location / dialect / coordinates (see commit message / PR for evidence).
NULL_BACKFILL = {
    "BG-019": "BG",   # Vidin
    "EST-001": "EE",  # Kohila, Estonia
    "GR-037": "GR",   # Crete
    "MK-001x": "MK",  # Prilep
    "RO-069": "RO",   # Mureș region coordinates
    "TR-003": "TR",   # ref prefix; record otherwise empty
    "PUB-066": "DE",  # "Manisch", coordinates in Hesse, Germany
}

# Refs that are import artifacts with no linguistic content — left untouched.
IGNORE_REFS = {"gurbet", "Gurbet Varieties"}


class Command(BaseCommand):
    help = "Normalize Sample.country_code to ISO 3166-1 alpha-2 and backfill NULLs."

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Report the changes that would be made without writing.",
        )

    def handle(self, *args, **options):
        dry_run = options["dry_run"]
        db = Sample.db()
        collection = db.collection(Sample.collection_name)

        cursor = db.aql.execute(
            "FOR s IN Samples RETURN {_key: s._key, ref: s.sample_ref, cc: s.country_code}"
        )
        rows = list(cursor)

        remapped = 0
        backfilled = 0
        unchanged = 0
        skipped_artifacts = 0
        unresolved = []

        for row in rows:
            ref = row["ref"]
            current = row["cc"]
            target = None

            if ref in IGNORE_REFS:
                skipped_artifacts += 1
                continue

            if current in (None, ""):
                target = NULL_BACKFILL.get(ref)
                if target is None:
                    unresolved.append(ref)
                    continue
                action = "backfill"
            elif current in ISO_REMAP:
                target = ISO_REMAP[current]
                action = "remap"
            else:
                # Already alpha-2 or a deliberately kept code.
                unchanged += 1
                continue

            if current == target:
                unchanged += 1
                continue

            self.stdout.write(
                f"  {ref}: {current!r} -> {target!r} ({action})"
            )
            if not dry_run:
                collection.update({"_key": row["_key"], "country_code": target}, merge=True)

            if action == "backfill":
                backfilled += 1
            else:
                remapped += 1

        self.stdout.write("")
        self.stdout.write(self.style.SUCCESS(
            f"{'[dry-run] ' if dry_run else ''}"
            f"remapped={remapped} backfilled={backfilled} "
            f"unchanged={unchanged} artifacts_skipped={skipped_artifacts}"
        ))
        if unresolved:
            self.stdout.write(self.style.WARNING(
                f"still NULL (no rule): {', '.join(unresolved)}"
            ))
