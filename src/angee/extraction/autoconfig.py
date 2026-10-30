"""Extraction profile and acquisition bounds."""

from typing import TypedDict

_Settings = TypedDict(
    "_Settings",
    {
        "ANGEE_EXTRACTION_PROFILE_CLASSES": dict[str, str],
        "ANGEE_EXTRACTION_MAX_BYTES": int,
    },
)

SETTINGS: _Settings = {
    "ANGEE_EXTRACTION_PROFILE_CLASSES": {
        "none": "angee.extraction.profiles.UnconfiguredExtractionProfile",
        "plain_text": "angee.extraction.profiles.PlainTextExtractionProfile",
    },
    "ANGEE_EXTRACTION_MAX_BYTES": 25 * 1024 * 1024,
}
