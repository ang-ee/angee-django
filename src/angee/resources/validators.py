"""Resource identity value validation shared by declaration and adapter boundaries."""

from typing import Any

from angee.resources.exceptions import ResourceLoadError


def normalize_xref(value: Any, *, location: str) -> str:
    """Require one nonblank addon-local xref while preserving source diagnostics."""

    if not isinstance(value, str) or not value.strip():
        raise ResourceLoadError(f"{location}: missing _xref")
    return value.strip()
