"""Angee model, identity and permission foundation."""
from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from angee.base.provision import require_provisioned

__all__ = ["require_provisioned"]


def __getattr__(name: str) -> Any:
    """Expose provision readiness without loading models for primitive imports."""
    if name == "require_provisioned":
        from angee.base.provision import require_provisioned

        return require_provisioned
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")
