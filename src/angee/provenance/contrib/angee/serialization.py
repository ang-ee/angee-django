"""Creation fingerprints for Angee model and write inputs."""

from __future__ import annotations

from collections.abc import Mapping
from enum import Enum
from typing import Any

from django.db import models

from angee.base.serialization import canonical_json_sha256, json_safe


def creation_fingerprint(values: Any) -> str:
    """Return the canonical fingerprint shared by every idempotent create."""

    def content(value: Any) -> Any:
        if isinstance(value, Enum):
            return content(value.value)
        if isinstance(value, models.Model):
            return {"model": value._meta.label_lower, "pk": json_safe(value.pk)}
        if isinstance(value, Mapping):
            return {key: content(item) for key, item in value.items()}
        if isinstance(value, (tuple, list)):
            return [content(item) for item in value]
        return json_safe(value)

    return canonical_json_sha256(content(values))
