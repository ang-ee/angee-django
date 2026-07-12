"""Generic record references backed by Django contenttypes."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, ClassVar

from django.contrib.contenttypes.models import ContentType
from django.db import models
from rebac.resources import model_resource_type

from angee.base.models import public_id_for


@dataclass(frozen=True, slots=True)
class RecordRef:
    """Frozen public identity for a model row reached through a generic pointer."""

    model_label: str
    object_id: Any | None
    public_id: str
    resource_type: str


def record_ref_for(instance: models.Model) -> RecordRef:
    """Return the stable public reference for ``instance``."""

    model = type(instance)
    return _record_ref_from_model(model, instance.pk)


class RecordRefMixin(models.Model):
    """Project a contenttypes-backed row reference from model-owned fields."""

    record_ref_field_prefix: ClassVar[str] = "target"
    """Reference field prefix; ``target`` maps to ``content_type``/``object_id``."""

    class Meta:
        """Django model options for record-ref-only abstract inheritance."""

        abstract = True

    @property
    def record_ref(self) -> RecordRef:
        """Return this row's referenced record identity without loading the target."""

        content_type_id = getattr(self, self._record_ref_content_type_id_attr(), None)
        object_id = getattr(self, self._record_ref_object_id_field_name(), None)
        if content_type_id in (None, "") or object_id in (None, ""):
            return _empty_record_ref(object_id)
        model = ContentType.objects.get_for_id(content_type_id).model_class()
        if model is None:
            return _empty_record_ref(object_id)
        return _record_ref_from_model(model, object_id)

    @property
    def record_model_label(self) -> str:
        """Return the referenced record's ``app_label.ModelName`` label."""

        return self.record_ref.model_label

    @property
    def record_public_id(self) -> str:
        """Return the referenced record's stable public id."""

        return self.record_ref.public_id

    @classmethod
    def _record_ref_content_type_field_name(cls) -> str:
        """Return the content-type FK field that backs this reference."""

        prefix = cls.record_ref_field_prefix
        if prefix == "target":
            return "content_type"
        return f"{prefix}_content_type"

    @classmethod
    def _record_ref_content_type_id_attr(cls) -> str:
        """Return the stored content-type id attribute name."""

        return f"{cls._record_ref_content_type_field_name()}_id"

    @classmethod
    def _record_ref_object_id_field_name(cls) -> str:
        """Return the object-id field that backs this reference."""

        prefix = cls.record_ref_field_prefix
        if prefix == "target":
            return "object_id"
        return f"{prefix}_object_id"


def _record_ref_from_model(model: type[models.Model], object_id: Any) -> RecordRef:
    """Return a record ref from an already resolved model and primary key."""

    return RecordRef(
        model_label=model._meta.label,
        object_id=object_id,
        public_id=public_id_for(model, object_id),
        resource_type=model_resource_type(model) or "",
    )


def _empty_record_ref(object_id: Any | None = None) -> RecordRef:
    """Return the empty reference used for unset or stale contenttypes."""

    return RecordRef(model_label="", object_id=object_id, public_id="", resource_type="")
