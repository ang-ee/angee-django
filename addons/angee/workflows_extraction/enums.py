"""Owned extraction role and outcome vocabulary shared by retention and callers."""

from enum import StrEnum

from django.db.models import TextChoices

from angee.agents.models import IMAGE_INFERENCE_MODEL_USES, TEXT_INFERENCE_MODEL_USES, InferenceModelUse


class ExtractionRole(StrEnum):
    """Inference roles and their accepted model capabilities."""

    MAPPING = "mapping"
    RECOGNITION = "recognition"

    @property
    def accepted_model_uses(self) -> frozenset[InferenceModelUse]:
        """Return the model uses accepted by this extraction role."""

        return {
            self.MAPPING: TEXT_INFERENCE_MODEL_USES,
            self.RECOGNITION: IMAGE_INFERENCE_MODEL_USES,
        }[self]


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
