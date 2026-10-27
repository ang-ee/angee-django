"""Native merge lifecycle integration for provenance attribution."""

from __future__ import annotations

from contextlib import ExitStack
from typing import Any

from django.db import models

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
