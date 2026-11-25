"""Message template deletion refusals on instance, queryset and cascade deletion paths."""

from __future__ import annotations

from typing import Any

from django.core.exceptions import ValidationError
from django.db.models.signals import pre_delete

from angee.base.signals import connect_for_models, starts_delete
from angee.messaging_templates.models import MessageTemplate


def connect() -> None:
    """Wire the refusal to concrete message templates."""

    connect_for_models(
        pre_delete,
        refuse_unresolved_delete,
        applies=lambda model: issubclass(model, MessageTemplate),
        dispatch_uid="angee.messaging_templates.message_template.delete_blocker",
    )


def refuse_unresolved_delete(sender: Any, instance: MessageTemplate, *, origin: Any, **kwargs: Any) -> None:
    """Refuse a delete :meth:`MessageTemplate.delete_blocker` refuses, judging the row as locked for the delete.

    Every origin rereads through :meth:`~angee.base.models.base.AngeeModel.lock_for_delete`.
    """

    current = instance.lock_for_delete()
    if current is None:
        return
    if blocker := current.delete_blocker(cascaded=not starts_delete(instance, origin)):
        raise ValidationError(blocker)
