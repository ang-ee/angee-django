"""Exceptions for addon resource loading.

A leaf module so resource modules (``entries``, ``fetch``, …) share one error
type without importing each other.
"""

from __future__ import annotations


class ResourceLoadError(RuntimeError):
    """Raised when resource rows cannot be loaded."""
