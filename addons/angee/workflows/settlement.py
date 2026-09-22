"""Explicit subject-type settlement declarations for terminal workflow runs."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

from django.apps import apps
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
from django.db import models
from django.utils.module_loading import import_string

from angee.base.db import get_write_alias, related_on


def subject_settlement_handlers() -> dict[tuple[str, str], Callable[..., None]]:
    """Expand declared model bases into exact content-type natural keys.

    ``ANGEE_WORKFLOW_SUBJECT_SETTLERS`` maps a model class's dotted import
    path to a handler's dotted path. Abstract bases explicitly include their
    installed concrete subclasses; no content-type query or mutable registry
    is needed. Overlapping declarations fail instead of selecting by order.
    """

    handlers: dict[tuple[str, str], Callable[..., None]] = {}
    models_by_label = sorted(apps.get_models(), key=lambda model: model._meta.label_lower)
    for declaration, handler_path in sorted(getattr(settings, "ANGEE_WORKFLOW_SUBJECT_SETTLERS", {}).items()):
        base = import_string(declaration)
        handler = import_string(handler_path)
        if not isinstance(base, type) or not issubclass(base, models.Model) or not callable(handler):
            raise ImproperlyConfigured(f"Invalid subject settlement declaration {declaration!r}.")
        for model in models_by_label:
            if not issubclass(model, base):
                continue
            key = (model._meta.app_label, model._meta.model_name)
            if key in handlers:
                raise ImproperlyConfigured(f"Multiple subject settlement handlers declare {model._meta.label}.")
            handlers[key] = handler
    return handlers


def settle_subject(run: Any, *, using: str | None = None) -> None:
    """Invoke the explicitly registered owner for this run's subject type.

    The dispatch owner holds the terminal run lock and consumes its intent only
    after this database-only handler succeeds. Handlers own subject locking and
    idempotence against a newer operation on that subject.
    """

    alias = get_write_alias(type(run), using=using, instance=run)
    content_type = related_on(run, "subject_content_type", using=alias)
    if content_type is None:
        return
    handler = subject_settlement_handlers().get((content_type.app_label, content_type.model))
    if handler is not None:
        handler(run, using=alias)
