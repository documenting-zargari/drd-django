"""
Sample-scope filters shared by every cross-sample search (answers, phrases,
transcriptions, concordance): narrow the samples searched by country,
contact language (L2), dialect group, source type and migrant status.

Requests carry them as (body or query-string) params:

- ``country_codes``: ISO alpha-2 codes; ``__none__`` = no country recorded.
  Legacy un-normalized codes (e.g. "FIN") still match, see
  ``data.country_codes.expand_with_legacy_aliases``.
- ``contact_languages``: ``"<level>:<language>"`` tokens, e.g.
  ``"Current-L2:Russian"`` / ``"any:Turkish"`` (see
  ``data.origin_languages.parse_contact_language_filters``); a sample
  matches if it lists any of them.
- ``dialect_groups``: ``Sample.dialect_group_name`` values; ``__none__`` =
  no group recorded.
- ``source_type``: one ``Sample.source_type`` value (RMS Questionaire /
  Published Source / External Fieldwork); the types are exclusive.
- ``migrant``: ``all`` (default) / ``exclude``.

Each list filter matches any of its values; different filters combine with
AND. The sample set is small (~500), so it's fetched once per request and
filtered here rather than in AQL.
"""

from dataclasses import dataclass

from data.country_codes import expand_with_legacy_aliases
from data.origin_languages import parse_contact_language_filters

NO_VALUE = "__none__"
MIGRANT_MODES = ("all", "exclude")

_SAMPLES_AQL = (
    "FOR s IN Samples RETURN {sample_ref: s.sample_ref, country_code: s.country_code,"
    " contact_languages: s.contact_languages, dialect_group_name: s.dialect_group_name,"
    " source_type: s.source_type, migrant: s.migrant}"
)


@dataclass(frozen=True)
class SampleFilters:
    country_codes: frozenset = frozenset()
    contact_languages: tuple = ()  # parsed {level, language} dicts
    dialect_groups: frozenset = frozenset()
    source_type: str = ""  # one value; a sample has exactly one source type
    migrant: str = "all"

    def is_active(self):
        return bool(
            self.country_codes or self.contact_languages or self.dialect_groups
            or self.source_type or self.migrant != "all"
        )

    def matches(self, sample):
        return (
            _in_or_none(sample.get("country_code"), self.country_codes)
            and _in_or_none(sample.get("dialect_group_name"), self.dialect_groups)
            and (not self.source_type or sample.get("source_type") == self.source_type)
            and _migrant_matches(sample.get("migrant"), self.migrant)
            and _contact_languages_match(sample.get("contact_languages"), self.contact_languages)
        )


def _in_or_none(value, wanted):
    if not wanted:
        return True
    if value in (None, ""):
        return NO_VALUE in wanted
    return value in wanted


def _migrant_matches(value, mode):
    if mode == "all":
        return True
    return (value == "Yes") == (mode == "only")


def _contact_languages_match(entries, filters):
    if not filters:
        return True
    for e in entries if isinstance(entries, list) else []:
        language = (e.get("language") or "").strip().lower()
        if any(f["language"] == language and f["level"] in (None, e.get("level")) for f in filters):
            return True
    return False


def parse_sample_filters(data):
    """SampleFilters from a request body dict or a QueryDict (repeated or
    comma-separated params)."""
    def values(key):
        if hasattr(data, "getlist"):
            raw = [part for item in data.getlist(key) for part in item.split(",")]
        else:
            raw = data.get(key) or []
            raw = raw.split(",") if isinstance(raw, str) else raw
        return [v.strip() for v in raw if isinstance(v, str) and v.strip()]

    countries = set(values("country_codes"))
    migrant = str(data.get("migrant") or "all").lower()
    return SampleFilters(
        country_codes=frozenset(expand_with_legacy_aliases(countries - {NO_VALUE}) | (countries & {NO_VALUE})),
        contact_languages=tuple(parse_contact_language_filters(values("contact_languages"))),
        dialect_groups=frozenset(values("dialect_groups")),
        source_type=(values("source_type") or [""])[0],
        migrant=migrant if migrant in MIGRANT_MODES else "all",
    )


def filter_sample_refs(db, filters, sample_refs=None):
    """Refs of samples passing `filters`, narrowed to `sample_refs` if given.

    None when no filter is active (i.e. "no narrowing"); otherwise a sorted
    list, which may be empty. Visibility is NOT applied here — callers keep
    their own authorization step.
    """
    if not filters.is_active():
        return None
    wanted = set(sample_refs) if sample_refs else None
    return sorted(
        s["sample_ref"]
        for s in db.aql.execute(_SAMPLES_AQL)
        if filters.matches(s) and (wanted is None or s["sample_ref"] in wanted)
    )


def resolve_sample_scope(db, data, sample_refs, visible_refs):
    """
    Final list of sample refs for a search: the caller's explicit
    `sample_refs` intersected with `visible_refs` (or, when none given,
    `visible_refs` itself), then narrowed by the request's sample filters.

    `visible_refs` must already be the caller's authorized set (every sample
    if they've opted into hidden ones, else visible == 'Yes' only). An
    explicit `sample_refs` is never trusted on its own: the client is not an
    authorization boundary (a stale client-side cache once handed back a
    hidden sample_ref for an anonymous session, 2026-09-16), so a ref the
    requester isn't authorized to see is silently dropped rather than served.
    """
    base = (set(sample_refs) & set(visible_refs)) if sample_refs else set(visible_refs)
    filtered = filter_sample_refs(db, parse_sample_filters(data))
    if filtered is not None:
        base &= set(filtered)
    return list(base)
