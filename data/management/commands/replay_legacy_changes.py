"""
Apply a changeset produced by `diff_legacy_dump` to the (refactored-schema)
ArangoDB that the current Django settings point at.

Background
----------
See data/legacy_replay/__init__.py for the full story. In short: production
kept editing the pre-refactor schema after the MasterPhrases/SamplePhrases
migration baseline was cut, and this replays those edits onto the new
schema. Collections are handled three ways:

  - DIRECT_PASSTHROUGH: same _key space/fields in both eras -> copied as-is.
  - SPECIAL: has a registered translator in legacy_replay/translators.py
    (Phrases -> SamplePhrases, Answers field-stripping, Translations
    re-keying).
  - DEAD: pre-refactor edge collections confirmed unread by the app
    (grepped data/views.py) — reported, never written.

Anything else is reported as "no translator registered" and left untouched;
extend legacy_replay/translators.py rather than guessing here.

Safe to run repeatedly: every translator checks the target document before
writing, so a second run over the same changeset is a no-op. Dry-run by
default — pass --apply to actually write.

Usage:
    python manage.py replay_legacy_changes changeset.json
    python manage.py replay_legacy_changes changeset.json --apply
    python manage.py replay_legacy_changes changeset.json --apply --only Phrases,Answers
    python manage.py replay_legacy_changes changeset.json --resolve-conflicts=production

A 'changed' doc where production and the live target disagree is always
reported as a CONFLICT rather than silently applied (some fields — e.g.
Transcriptions.question_ids — are independently recomputed on both sides by
backfill scripts, not user-edited, so a same-shape diff can mean either a
genuine production edit or a stale/narrower snapshot of the same
computation; guessing wrong risks silent data loss). Pass
--resolve-conflicts=production to apply production's value to every
reported conflict once you've reviewed them — there is currently no
opposite "target wins" option; unresolved conflicts stay untouched either
way, ready for a future replay once they're settled.
"""

import json
from functools import partial
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError

from data.legacy_replay import DEAD, DIRECT_PASSTHROUGH, SPECIAL, load_dump_collection
from data.legacy_replay.translators import TRANSLATORS, translate_direct
from data.models import MasterPhrase


class Command(BaseCommand):
    help = "Replay a diff_legacy_dump changeset onto the current schema's ArangoDB."

    def add_arguments(self, parser):
        parser.add_argument("changeset", help="Path to the changeset JSON produced by diff_legacy_dump.")
        parser.add_argument("--apply", action="store_true", help="Write changes. Default is dry run.")
        parser.add_argument("--only", help="Comma-separated collection names to replay; default: all in the changeset.")
        parser.add_argument(
            "--resolve-conflicts",
            choices=["production"],
            help="Apply production's value to every reported conflict instead of just reporting it. "
            "Review the conflicts (run without this flag first) before using it.",
        )

    def handle(self, *args, **options):
        changeset_path = Path(options["changeset"])
        if not changeset_path.is_file():
            raise CommandError(f"{changeset_path} not found")
        changeset = json.loads(changeset_path.read_text())

        baseline_dir = Path(changeset["baseline"])
        current_dir = Path(changeset["current"])
        if not baseline_dir.is_dir() or not current_dir.is_dir():
            raise CommandError(
                "The changeset's --baseline/--current directories are no longer at their original "
                "paths; re-run diff_legacy_dump or move them back."
            )

        dry_run = not options["apply"]
        only = set(c.strip() for c in options["only"].split(",")) if options["only"] else None
        force_conflicts = options["resolve_conflicts"] == "production"
        db = MasterPhrase.db()

        anchor_to_ref = None
        if "Translations" in changeset["collections"] and (only is None or "Translations" in only):
            anchors = load_dump_collection(baseline_dir, "PhraseAnchors") or {}
            anchor_to_ref = {a["id"]: a["phrase_ref"] for a in anchors.values()}

        self.stdout.write(self.style.WARNING("DRY RUN — pass --apply to write changes\n") if dry_run else "")

        for name, delta in changeset["collections"].items():
            if only is not None and name not in only:
                continue
            added, removed, changed = delta["added"], delta["removed"], delta["changed"]
            if not (added or removed or changed):
                continue

            self.stdout.write(f"\n{name}: +{len(added)} -{len(removed)} ~{len(changed)}")

            if name in DEAD:
                self.stdout.write(
                    f"  SKIPPED — {name} is dead (unread by the app since before the refactor baseline; "
                    f"see data/legacy_replay/__init__.py). Not replayed."
                )
                continue

            baseline_docs = load_dump_collection(baseline_dir, name) or {}
            current_docs = load_dump_collection(current_dir, name) or {}

            if name in SPECIAL:
                translator = TRANSLATORS[name]
                if name == "Translations":
                    translator = partial(translator, anchor_to_ref=anchor_to_ref)
                elif name == "Answers":
                    translator = partial(translator, force_conflicts=force_conflicts)
            elif name in DIRECT_PASSTHROUGH:
                translator = partial(translate_direct(name), force_conflicts=force_conflicts)
            else:
                self.stdout.write(
                    self.style.ERROR(
                        f"  NO TRANSLATOR registered for {name} — skipped. "
                        f"Add one to data/legacy_replay/translators.py before replaying this collection."
                    )
                )
                continue

            counts = translator(added, removed, changed, baseline_docs, current_docs, db, dry_run, self.stdout)
            self.stdout.write(f"  {counts}")

        self.stdout.write(self.style.SUCCESS("\nDone (dry run)." if dry_run else "\nDone."))
