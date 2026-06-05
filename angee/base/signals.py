"""Runtime registration for revision-enabled models."""

from __future__ import annotations

import reversion
from django.apps import apps


def register_revision_models() -> None:
    """Register revision-enabled loaded models with reversion."""

    for model in apps.get_models():
        fields = getattr(model, "revisioned_fields", ())
        if fields and not reversion.is_registered(model):
            reversion.register(model, fields=list(fields))
