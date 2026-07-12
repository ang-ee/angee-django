"""Runtime-importable declarations consumed by the model emitter.

Base owns these inert dataclasses because runtime mixins construct them when
declaring build-time emission seams. Only ``angee.compose`` interprets the
payloads; moving this vocabulary into compose would make runtime code import the
build-time composer, inverting Angee's dependency direction.
"""

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
