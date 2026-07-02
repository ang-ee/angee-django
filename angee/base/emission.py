"""Declarations consumed by the runtime model emitter."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True, slots=True)
class ModelDecorator:
    """Decorator the composer applies to emitted concrete models."""

    import_path: str
    args: tuple[Any, ...] = ()
    kwargs: tuple[tuple[str, Any], ...] = ()
    kwargs_from_model: tuple[tuple[str, str], ...] = ()
    enabled_by_model_attr: str = ""


@dataclass(frozen=True, slots=True)
class ModelClassAttribute:
    """Class-body attribute the composer emits on concrete models."""

    name: str
    import_path: str
    args: tuple[Any, ...] = ()
    kwargs: tuple[tuple[str, Any], ...] = ()
