"""Portable retained references and JSON facts; public reads follow host policy."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from typing import Any, cast

from django.apps import apps
from django.core.exceptions import ValidationError
from django.core.serializers.json import DjangoJSONEncoder
from django.db import models

from angee.provenance.engine.policy import get_policy


@dataclass(frozen=True)
class EvidenceReference:
    """A retained Django model identity, not an authorization grant."""

    model: str
    pk: str

    @classmethod
    def from_record(cls, record: models.Model) -> EvidenceReference:
        if record.pk is None or record._state.adding:
            raise ValidationError("Evidence records must be persisted.")
        return cls(record._meta.label_lower, str(record.pk))

    def resolve(self, *, actor: object, using: str | None = None) -> models.Model:
        model = apps.get_model(self.model)
        record = model._base_manager.db_manager(using).get(pk=self.pk)
        get_policy().authorize(actor, "view", record)
        return cast(models.Model, record)


@dataclass(frozen=True)
class EvidenceFact:
    """A JSON-compatible assertion retained with its optional source reference."""

    value: Any
    source: EvidenceReference | None = None


def evidence_values(values: object) -> list[dict[str, Any]]:
    """Validate evidence before any assertion is written."""
    if values is None:
        return []
    if not isinstance(values, (list, tuple)):
        raise ValidationError("Evidence must be a sequence of references or facts.")
    encoded: list[dict[str, Any]] = []
    for value in values:
        if isinstance(value, models.Model):
            value = EvidenceReference.from_record(value)
        if isinstance(value, EvidenceReference):
            encoded.append({"reference": asdict(value)})
        elif isinstance(value, EvidenceFact):
            encoded.append({"fact": asdict(value)})
        elif isinstance(value, dict):
            encoded.append(value)
        else:
            raise ValidationError("Evidence requires a typed reference or JSON fact mapping.")
    try:
        return cast(list[dict[str, Any]], json.loads(json.dumps(encoded, cls=DjangoJSONEncoder)))
    except (TypeError, ValueError) as error:
        raise ValidationError("Evidence facts must be JSON-compatible.") from error
