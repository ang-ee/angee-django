"""Path coercion shared by Angee boot modules."""

from __future__ import annotations

from os import PathLike
from pathlib import Path
from typing import Any

from django.core.exceptions import ImproperlyConfigured


def resolve_path(value: Any) -> Path:
    """Return ``value`` as an absolute path."""

    if not isinstance(value, str | PathLike):
        raise ImproperlyConfigured(f"Expected path setting value, got {value!r}")
    try:
        return Path(value).expanduser().resolve()
    except (OSError, TypeError, ValueError) as error:
        raise ImproperlyConfigured(f"Expected path setting value, got {value!r}") from error
