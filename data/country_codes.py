"""Shared Sample.country_code normalization mapping.

The legacy RMS database stored ``country_code`` using the same ad-hoc scheme
as the sample_ref prefixes ("EST", "FIN", "RUS", ...) rather than ISO 3166-1
alpha-2. `normalize_country_codes` (a one-off management command) rewrites
stored values to alpha-2, but nothing enforces that at write time, so legacy
values can still be reintroduced (e.g. via CSV import) or simply never get
cleaned up on a given database. Query-side code must therefore tolerate both
forms rather than assuming normalization has run.

See ``data/management/commands/normalize_country_codes.py`` for the one-off
cleanup command that uses this same mapping.
"""

# Non-standard stored code -> ISO 3166-1 alpha-2.
# Codes already valid alpha-2 (AL, AT, BG, CZ, DE, FR, GB, GR, HR, HU, IR, IT,
# LT, LV, MD, MK, MX, NO, PL, RO, SE, SK, TR) are intentionally absent.
ISO_REMAP = {
    "EST": "EE",  # Estonia
    "FIN": "FI",  # Finland
    "RUS": "RU",  # Russia
    "UKR": "UA",  # Ukraine
    "SLO": "SI",  # Slovenia (Prekmurje varieties; Slovakia is the separate "SK" set)
    "SP": "ES",   # Spain
    "N": "NO",    # Norway (single PUB sample, Trondheim)
}

# Reverse index: ISO alpha-2 -> legacy raw codes that normalize to it.
LEGACY_BY_ISO = {}
for _legacy, _iso in ISO_REMAP.items():
    LEGACY_BY_ISO.setdefault(_iso, []).append(_legacy)
del _legacy, _iso


def expand_with_legacy_aliases(codes):
    """Given canonical ISO alpha-2 codes, return them plus any legacy raw
    values that normalize to them (e.g. "FI" -> {"FI", "FIN"}).

    Callers filtering Sample.country_code should expand a requested code set
    through this before querying, so un-normalized records still match.
    """
    expanded = set(codes)
    for code in codes:
        expanded.update(LEGACY_BY_ISO.get(code, ()))
    return expanded
