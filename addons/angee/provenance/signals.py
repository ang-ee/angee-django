"""Native operation boundaries that contribute provenance attribution."""

from __future__ import annotations

from contextlib import ExitStack
from typing import Any

from django.db import models

from angee.provenance.context import changeset, current_changeset
from angee.provenance.contrib.angee.context import merge_context


def capture_merge(
    sender: type[models.Model],
    *,
    survivor: models.Model,
    join: bool,
    contexts: ExitStack,
    **kwargs: Any,
) -> None:
    """Attribute every fold, even when only its referencing models keep history."""

    contexts.enter_context(merge_context(survivor, join=join))


def capture_resource_load(sender: type[models.Model], *, contexts: ExitStack, **kwargs: Any) -> None:
    """Capture one resource batch with seed attribution inside its transaction."""

    contexts.enter_context(changeset(fresh=True, activity="seed", using=kwargs.get("using")))
    current_changeset(record=True)
