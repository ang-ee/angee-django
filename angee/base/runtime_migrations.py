"""The identity an addon runtime migration carries once the composer materializes it.

Serving code may recognize a materialized migration (a ``pre_migrate`` receiver
reading the plan) without importing the build-time composer, which writes it.
"""

from __future__ import annotations

from typing import Any

ORIGIN_ATTR = "angee_origin"
"""The ``Migration`` class attribute naming its ``<addon>:<name>`` origin."""


def runtime_migration_origin(migration: Any) -> str | None:
    """Return the ``<addon>:<name>`` origin a materialized addon runtime migration records."""

    return getattr(migration, ORIGIN_ATTR, None)
