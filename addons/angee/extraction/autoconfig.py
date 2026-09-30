"""Extraction profile and acquisition bounds."""

SETTINGS = {
    "ANGEE_IMPL_REGISTRIES:append": ["angee.extraction.profiles.ExtractionProfile"],
    "ANGEE_EXTRACTION_PROFILE_CLASSES": {"none": "angee.extraction.profiles.UnconfiguredExtractionProfile"},
    "ANGEE_EXTRACTION_MAX_BYTES": 25 * 1024 * 1024,
}
