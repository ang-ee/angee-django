"""The resource tier vocabulary.

A leaf module so the ``AppConfig`` (which reads tiers while normalizing its
``resources`` manifest) can name them without importing the ``Resource`` model
and its manager/loader chain.
"""

from __future__ import annotations

from django.core.exceptions import ImproperlyConfigured
from django.db import models


class ResourceTier(models.TextChoices):
    """Resource file tiers persisted on ledger rows."""

    MASTER = "master", "Master"
    INSTALL = "install", "Install"
    DEMO = "demo", "Demo"

    @classmethod
    def from_value(cls, value: object) -> str:
        """Return a tier value from a ``ResourceTier`` or string shorthand."""

        if isinstance(value, cls):
            return value.value
        raw = str(value)
        try:
            return cls(raw).value
        except ValueError as exc:
            expected = ", ".join(cls.values)
            raise ImproperlyConfigured(
                f"Unknown resource tier {raw!r}; expected one of {expected}"
            ) from exc
