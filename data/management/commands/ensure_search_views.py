"""
Idempotent provisioning of the ArangoSearch analyzers and views that the
search / concordance endpoints depend on.

Historically the ``norm_lower`` analyzer and the ``TranscriptionSearch`` /
``SamplePhraseSearch`` views were created only by the external ETL pipeline
(``extract/manual_extraction/extract.py``, a separate repo). This command lets
an existing database be brought up to date without a full re-import, and is the
source of truth for the concordance feature's tokenising analyzers.

What it ensures:

* ``norm_lower``      - norm analyzer (lowercase, accent-folded). Pre-existing;
                        (re)created here only if missing so a fresh DB works.
* ``concord``         - segmentation analyzer, accent-PRESERVING whole-word
                        tokens (``šavo`` != ``savo``). Backs "match diacritics".
* ``concord_fold``    - text analyzer, stemming disabled, accent-FOLDED
                        whole-word tokens. Backs the default loose matching.
* ``TranscriptionSearch`` links ``transcription`` / ``english`` with all three
  analyzers; ``SamplePhraseSearch`` links ``phrase`` with all three.

Safe to run repeatedly. ``--force`` deletes and recreates the analyzers and
views from scratch (triggers a reindex).

Usage:
    python manage.py ensure_search_views --dry-run
    python manage.py ensure_search_views
    python manage.py ensure_search_views --force
"""

from django.core.management.base import BaseCommand

from data.models import Sample

# name -> (type, properties, features)
ANALYZERS = {
    "norm_lower": (
        "norm",
        {"locale": "en", "case": "lower", "accent": False},
        ["frequency", "position", "norm"],
    ),
    "concord": (
        "segmentation",
        {"break": "alpha", "case": "lower"},
        ["frequency", "position", "norm"],
    ),
    "concord_fold": (
        "text",
        {
            "locale": "en",
            "case": "lower",
            "accent": False,
            "stemming": False,
            "stopwords": [],
        },
        ["frequency", "position", "norm"],
    ),
}

TOKEN_ANALYZERS = ["norm_lower", "concord", "concord_fold"]

# view name -> (collection, {field: [analyzers]})
VIEWS = {
    "TranscriptionSearch": (
        "Transcriptions",
        {"transcription": TOKEN_ANALYZERS, "english": TOKEN_ANALYZERS, "sample": []},
    ),
    "SamplePhraseSearch": (
        "SamplePhrases",
        {"phrase": TOKEN_ANALYZERS, "sample": []},
    ),
}


def _link(collection, field_analyzers):
    fields = {}
    for field, analyzers in field_analyzers.items():
        fields[field] = {"analyzers": analyzers} if analyzers else {}
    return {
        collection: {
            "analyzers": ["identity"],
            "fields": fields,
            "includeAllFields": False,
            "storeValues": "none",
            "trackListPositions": False,
        }
    }


class Command(BaseCommand):
    help = "Create/update the ArangoSearch analyzers and views used by search and concordance."

    def add_arguments(self, parser):
        parser.add_argument(
            "--dry-run",
            action="store_true",
            help="Report what would change without writing.",
        )
        parser.add_argument(
            "--force",
            action="store_true",
            help="Delete and recreate the analyzers and views from scratch.",
        )

    def handle(self, *args, **options):
        dry_run = options["dry_run"]
        force = options["force"]
        db = Sample.db()

        existing_analyzers = {a["name"].split("::")[-1] for a in db.analyzers()}
        existing_views = {v["name"] for v in db.views()}

        prefix = "[dry-run] " if dry_run else ""

        # --- analyzers -------------------------------------------------------
        for name, (atype, props, features) in ANALYZERS.items():
            present = name in existing_analyzers
            if present and force:
                self.stdout.write(f"{prefix}delete analyzer {name}")
                if not dry_run:
                    db.delete_analyzer(name, force=True, ignore_missing=True)
                present = False
            if present:
                self.stdout.write(f"  analyzer {name}: exists, left as-is")
                continue
            self.stdout.write(f"{prefix}create analyzer {name} ({atype})")
            if not dry_run:
                db.create_analyzer(name, atype, props, features)

        # --- views ---------------------------------------------------------
        for name, (collection, field_analyzers) in VIEWS.items():
            props = {"links": _link(collection, field_analyzers)}
            present = name in existing_views
            if present and force:
                self.stdout.write(f"{prefix}delete view {name}")
                if not dry_run:
                    db.delete_view(name, ignore_missing=True)
                present = False
            if present:
                self.stdout.write(
                    f"{prefix}update view {name} links -> {collection}"
                    f"({', '.join(f for f, a in field_analyzers.items() if a)}) "
                    f"[{', '.join(TOKEN_ANALYZERS)}]"
                )
                if not dry_run:
                    db.update_arangosearch_view(name, props)
            else:
                self.stdout.write(f"{prefix}create view {name}")
                if not dry_run:
                    db.create_arangosearch_view(name, props)

        self.stdout.write("")
        self.stdout.write(self.style.SUCCESS(f"{prefix}search views ready"))
