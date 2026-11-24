"""Brand kit deletion refusals on instance, queryset and cascade deletion paths."""

from __future__ import annotations

from typing import Any

from django.core.exceptions import ValidationError
from django.db.models.signals import pre_delete

from angee.base.signals import connect_for_models, starts_delete
from angee.brandkit.models import BrandKit


def connect() -> None:
    """Wire the refusal to concrete brand kits."""

    connect_for_models(
        pre_delete,
        refuse_default_delete,
        applies=lambda model: issubclass(model, BrandKit),
        dispatch_uid="angee.brandkit.brand_kit.delete_blocker",
    )


def refuse_default_delete(sender: Any, instance: BrandKit, *, origin: Any, **kwargs: Any) -> None:
    """Refuse a delete :meth:`BrandKit.delete_blocker` refuses; a direct delete rereads its possibly stale row."""

    current = type(instance)._base_manager.filter(pk=instance.pk).first() if origin is instance else instance
    if current is None:
        return
    if blocker := current.delete_blocker(cascaded=not starts_delete(instance, origin)):
        raise ValidationError(blocker)
