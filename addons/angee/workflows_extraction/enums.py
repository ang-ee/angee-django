"""Owned extraction failure vocabulary shared by retention and callers."""

from enum import StrEnum

from django.db.models import TextChoices


class ExtractionErrorCode(TextChoices, StrEnum):
    """Stable retained failure codes with domain-level recovery behavior."""

    IDENTITY_CORRESPONDENCE_REQUIRED = (
        "source_hold:identity_correspondence_required",
        "Identity correspondence required",
    )


class ExtractionStatus(TextChoices):
    """Terminal outcome retained for one extraction revision."""

    SUCCEEDED = "succeeded", "Succeeded"
    FAILED = "failed", "Failed"
