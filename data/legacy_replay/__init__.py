"""
Shared plumbing for diffing/replaying changes made on a legacy-schema
ArangoDB export against the refactored (MasterPhrases/SamplePhrases) schema.

Background
----------
Production kept running on the pre-refactor schema (Phrases/PhraseTags/
HasTag/...) for a while after local dev cut the MasterPhrases/SamplePhrases
migration baseline and moved on. Real users made real edits on production
in that window. Those edits need to be found and re-applied on top of the
new schema — a one-time reconciliation, but one that will happen again any
time a production `arangodump` is pulled after further divergence, so the
tooling is generic rather than a one-off script:

    manage.py diff_legacy_dump --baseline <dir> --current <dir> --output changeset.json
    manage.py replay_legacy_changes changeset.json --apply

See data/management/commands/diff_legacy_dump.py and replay_legacy_changes.py.
"""

import gzip
import json
from pathlib import Path


def discover_collections(dump_dir: Path) -> set[str]:
    """Collection names present in an arangodump directory (excludes _system collections)."""
    names = set()
    for f in dump_dir.glob("*.data.json.gz"):
        name = f.name[: -len(".data.json.gz")].rsplit("_", 1)[0]
        if name.startswith("_"):
            continue
        names.add(name)
    return names


def load_dump_collection(dump_dir: Path, name: str) -> dict | None:
    """{_key: doc} for one collection in an arangodump directory, or None if absent."""
    matches = sorted(Path(dump_dir).glob(f"{name}_*.data.json.gz"))
    if not matches:
        return None
    docs = {}
    with gzip.open(matches[0], "rt", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            doc = json.loads(line)
            docs[doc["_key"]] = doc
    return docs


def normalize_doc(doc: dict) -> str:
    """Canonical form for equality comparison: drop volatile Arango fields."""
    return json.dumps(
        {k: v for k, v in doc.items() if k not in ("_rev", "_id")},
        sort_keys=True,
        ensure_ascii=False,
    )


# --- Collection classification for replay -----------------------------------
#
# DIRECT_PASSTHROUGH: same collection name and _key space in both schema eras,
# same (or compatible) fields — add/update/delete by _key with no translation.
DIRECT_PASSTHROUGH = {
    "Samples",
    "Sources",
    "Categories",
    "ResearchQuestions",
    "Views",
    "Transcriptions",
}

# DEAD: existed pre-refactor, superseded and unread by the app since before
# the refactor baseline. Verified by grepping data/views.py: phrase/
# transcription linking now goes through Answer.question_id matching
# MasterPhrase.question_ids/category_ids (+ SamplePhrase.question_overrides),
# not these edge collections. Reported, never written.
DEAD = {
    "HasTag",
    "GivesAnswer",
    "IsParentCategory",
    "TranslatesTo",
    "PhraseAnchors",
    "PhraseTags",
    "Presents",
    "RawData",
    "HasPhrase",
    "HasTranscription",
}

# SPECIAL: collections with a registered custom translator in translators.py
# (Phrases -> SamplePhrases, Answers field-stripping, Translations re-keying).
SPECIAL = {"Phrases", "Answers", "Translations"}
