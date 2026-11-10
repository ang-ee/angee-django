"""Validated deployment budgets for extraction and bounded local graph reads."""

from typing import Self

from django.conf import settings
from pydantic import BaseModel, ConfigDict, Field


class GraphOptions(BaseModel):
    """Overrides live in ANGEE_KNOWLEDGE_GRAPH; vault selection remains the opt-in."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    idle_minutes: int = Field(default=5, ge=0, le=1440)
    gleaning_passes: int = Field(default=0, ge=0, le=1)
    llm_adjudication: bool = False
    trigram_threshold: float = Field(default=0.8, gt=0, le=1)
    hops: int = Field(default=2, ge=1, le=2)
    degree_cap: int = Field(default=20, ge=1, le=100)
    seed_limit: int = Field(default=20, ge=1, le=50)
    node_limit: int = Field(default=400, ge=50, le=2000)

    @classmethod
    def from_settings(cls) -> Self:
        return cls.model_validate(settings.ANGEE_KNOWLEDGE_GRAPH)


def default_entity_kinds() -> list[str]:
    """A fresh default vocabulary for each vault's extraction schema."""

    return sorted(["person", "organization", "place", "concept", "event"])
