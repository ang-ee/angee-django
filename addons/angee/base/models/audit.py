"""Audit attribution and its serializable retained-reference delete policy."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any, TypeVar

from django.conf import settings
from django.db import models
from rebac import system_context
from rebac.managers import TrackedQuerySet

from angee.base.permissions import actor_user_id, instance_actor
from angee.base.models.identity import update_fields_with_auto_now
from angee.base.models.append_only import AppendOnlyBaseQuerySet
from angee.base.querysets import _AngeeQuerySetMixin, by_key


_ModelT = TypeVar("_ModelT", bound=models.Model)


class RetainedReferenceQuerySet(_AngeeQuerySetMixin[_ModelT], TrackedQuerySet[_ModelT]):
    """Django collector updates dispatched to the record's declared persistence owner."""

    def update(self, **kwargs: Any) -> int:
        """Keep exact collector selection and alias while preserving native capture."""

        with system_context(reason="delete.retain"):
            rows = self.model._base_manager.db_manager(self.db, hints=self._hints).get_queryset()
            rows.query = self.query.chain()
            if isinstance(rows, AppendOnlyBaseQuerySet):
                return rows.owner_update(**kwargs)
            return rows.update(**kwargs)


def retained_set_null(collector: Any, field: Any, sub_objs: Iterable[models.Model], using: str) -> None:
    """Schedule retained-edge removal as system work inside Django's delete transaction."""

    rows: RetainedReferenceQuerySet[models.Model] = RetainedReferenceQuerySet(model=field.model, using=using)
    if isinstance(sub_objs, models.QuerySet):
        rows.query = sub_objs.query.chain()
        collector.add_field_update(field, None, rows)
    else:
        for batch in by_key(rows, [obj.pk for obj in sub_objs]):
            collector.add_field_update(field, None, batch)


setattr(retained_set_null, "lazy_sub_objs", True)



class AuditMixin(models.Model):
    """Add conventional user-owned audit foreign keys to a model."""

    created_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=retained_set_null,
        related_name="+",
    )
    """The user that created the row, when known."""

    updated_by = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=retained_set_null,
        related_name="+",
    )
    """The user that most recently updated the row, when known."""

    class Meta:
        """Django model options for audit-only abstract inheritance."""

        abstract = True

    def save(self, *args: Any, **kwargs: Any) -> None:
        """Persist the row after stamping user audit fields."""

        update_fields = kwargs.get("update_fields")
        if update_fields is not None:
            update_fields = set(update_fields)
            if not update_fields:
                super().save(*args, **kwargs)
                return

        user_id = actor_user_id(instance_actor(self))
        touched: set[str] = set()
        if user_id is not None:
            if self._state.adding:
                if getattr(self, "created_by_id", None) is None:
                    self.created_by_id = user_id
                    touched.add("created_by")
                if getattr(self, "updated_by_id", None) is None:
                    self.updated_by_id = user_id
                    touched.add("updated_by")
            else:
                self.updated_by_id = user_id
                touched.add("updated_by")

        if update_fields is not None:
            kwargs["update_fields"] = update_fields_with_auto_now(self, update_fields | touched)
        super().save(*args, **kwargs)
