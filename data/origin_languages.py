"""
Locate L2 language names stored on Answers.

Per the RLD data owner, an answer's ORIGIN is one of four levels (Inherited,
Current-L2, Recent-L2, Old-L2); *which* language each L2 level refers to is an
attribute of the Sample (``Sample.contact_languages``), not of the answer.
Legacy data nevertheless carries language names on answers, in several shapes:

* flat ``source`` + ``language``
* ``origin`` as ``{source, language}`` or as a string ``"Current-L2: Bulgarian"``
* ``base_origin`` as ``{source, language}``
* ``markers[i].origin`` as ``{source, language}``

``extract_origin_languages`` returns every such language with the attribute
path it was found at, so it can be backed up and compared against the sample.
"""

LEVELS = ("Inherited", "Current-L2", "Recent-L2", "Old-L2")
_LEVEL_BY_LOWER = {level.lower(): level for level in LEVELS}


def normalize_level(value):
    """Canonical spelling of an origin level, or the stripped input if unknown."""
    if not isinstance(value, str):
        return None
    stripped = value.strip()
    return _LEVEL_BY_LOWER.get(stripped.lower(), stripped or None)


def _from_origin_value(value):
    """(level, language) from an ``origin``-style value (object or string)."""
    if isinstance(value, dict):
        return normalize_level(value.get("source")), value.get("language")
    if isinstance(value, str) and ":" in value:
        level, language = value.split(":", 1)
        return normalize_level(level), language.strip()
    return None, None


def extract_origin_languages(answer):
    """Return ``[(path, level, language)]`` for every non-empty language on ``answer``."""
    found = []

    def add(path, level, language):
        if isinstance(language, str) and language.strip():
            found.append((path, level, language.strip()))

    add("language", normalize_level(answer.get("source")), answer.get("language"))
    add("origin", *_from_origin_value(answer.get("origin")))
    add("base_origin", *_from_origin_value(answer.get("base_origin")))
    for i, marker in enumerate(answer.get("markers") or []):
        if isinstance(marker, dict):
            add(f"markers[{i}].origin", *_from_origin_value(marker.get("origin")))
    return found


def sample_language_levels(contact_languages):
    """``{language_lower: {levels}}`` from a Sample's ``contact_languages``."""
    levels = {}
    for entry in contact_languages or []:
        if not isinstance(entry, dict) or not entry.get("language"):
            continue
        key = entry["language"].strip().lower()
        levels.setdefault(key, set()).add(normalize_level(entry.get("source")))
    return levels


def classify(language, level, sample_levels):
    """``ok`` | ``level_mismatch`` | ``not_in_sample`` | ``sample_has_no_contact_languages``."""
    if not sample_levels:
        return "sample_has_no_contact_languages"
    levels = sample_levels.get(language.lower())
    if levels is None:
        return "not_in_sample"
    return "ok" if level in levels else "level_mismatch"
