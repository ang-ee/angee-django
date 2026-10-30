"""Exceptions raised while validating or loading addon resources."""

from __future__ import annotations


class ResourceLoadError(RuntimeError):
    """Raised when resource declarations or rows cannot be loaded."""
