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
        levels.setdefault(key, set()).add(normalize_level(entry.get("level") or entry.get("source")))
    return levels


def classify(language, level, sample_levels):
    """``ok`` | ``level_mismatch`` | ``not_in_sample`` | ``sample_has_no_contact_languages``."""
    if not sample_levels:
        return "sample_has_no_contact_languages"
    levels = sample_levels.get(language.lower())
    if levels is None:
        return "not_in_sample"
    return "ok" if level in levels else "level_mismatch"


# --- Unification -----------------------------------------------------------
#
# Target format: an answer carries at most one ``origin`` level (string, one of
# LEVELS); ``base_origin`` and ``markers[i].origin`` are level strings too.
# ``source``, ``language``, ``preposition_origin`` and the ``{source, language}``
# / ``"Level: Language"`` variants are dropped (languages are preserved in
# ``legacy_origin_language`` by ``backup_origin_languages`` first).

LEGACY_TOP_LEVEL = ("source", "language", "preposition_origin")


def _level_of(value):
    """Level from any legacy origin-ish value (string, ``"L: lang"`` or object)."""
    if isinstance(value, dict):
        return normalize_level(value.get("source"))
    if isinstance(value, str):
        return normalize_level(value.split(":", 1)[0])
    return None


def unify_answer(answer):
    """Return ``(patch, problem)`` turning ``answer`` into the unified format.

    ``patch`` maps attributes to new values (``None`` = remove); it is ``{}``
    when the answer is already unified. ``problem`` is set (and ``patch`` is
    ``None``) when the answer can't be converted without losing information:
    two different top-level levels, or a value that isn't one of LEVELS.
    """
    levels = {
        lvl for lvl in (
            _level_of(answer.get("source")),
            _level_of(answer.get("origin")),
            _level_of(answer.get("preposition_origin")),
        ) if lvl
    }
    nested = [_level_of(answer.get("base_origin"))] + [
        _level_of(m.get("origin")) for m in answer.get("markers") or [] if isinstance(m, dict)
    ]
    unknown = sorted(lvl for lvl in levels | set(nested) if lvl and lvl not in LEVELS)
    if unknown:
        return None, f"unknown level: {', '.join(unknown)}"
    if len(levels) > 1:
        return None, f"conflicting levels: {', '.join(sorted(levels))}"

    patch = {}
    for key in LEGACY_TOP_LEVEL:
        if key in answer:
            patch[key] = None
    origin = levels.pop() if levels else None
    if answer.get("origin") != origin and not (origin is None and "origin" not in answer):
        patch["origin"] = origin

    if "base_origin" in answer:
        base = _level_of(answer["base_origin"])
        if answer["base_origin"] != base:
            patch["base_origin"] = base

    markers = answer.get("markers")
    if isinstance(markers, list):
        new_markers = []
        for m in markers:
            if isinstance(m, dict) and "origin" in m:
                m = dict(m)
                level = _level_of(m["origin"])
                if level:
                    m["origin"] = level
                else:
                    del m["origin"]
            new_markers.append(m)
        if new_markers != markers:
            patch["markers"] = new_markers
    return patch, None


def unify_contact_languages(contact_languages):
    """``contact_languages`` with each entry's ``source`` key renamed to ``level``."""
    out = []
    for entry in contact_languages or []:
        if isinstance(entry, dict) and "source" in entry:
            source = entry["source"]
            entry = {k: v for k, v in entry.items() if k != "source"}
            entry["level"] = normalize_level(entry.get("level") or source)
        out.append(entry)
    return out


# --- Contact-language sample filter ----------------------------------------
#
# "Current L2 = Russian" narrows the *samples* searched (it's a Sample
# attribute, see module docstring). Tokens are "<level>:<language>" with level
# one of the L2 LEVELS or "any"; several tokens match a sample having any one.

ANY_LEVEL = "any"


def parse_contact_language_filters(tokens):
    """``[{level, language}]`` (level None = any L2, language lowercased) from tokens."""
    filters = []
    for token in tokens or []:
        if not isinstance(token, str) or ":" not in token:
            continue
        level, language = token.split(":", 1)
        language = language.strip().lower()
        level = None if level.strip().lower() == ANY_LEVEL else normalize_level(level)
        if language and (level is None or level in LEVELS[1:]):
            filters.append({"level": level, "language": language})
    return filters


CONTACT_LANGUAGE_SAMPLES_AQL = """
FOR s IN Samples
  FILTER LENGTH(
    FOR e IN (IS_ARRAY(s.contact_languages) ? s.contact_languages : [])
      FOR f IN @filters
        FILTER LOWER(TRIM(e.language)) == f.language
          AND (f.level == null OR e.level == f.level)
        LIMIT 1
        RETURN 1
  ) > 0
  RETURN s.sample_ref
"""
