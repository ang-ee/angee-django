"""Resource tier choices used by the resource ledger and manifests."""

from __future__ import annotations

from collections.abc import Iterable

from django.core.exceptions import ImproperlyConfigured
from django.db import models


class ResourceTier(models.TextChoices):
    """Resource file tiers persisted on ledger rows."""

    MASTER = "master", "Master"
    INSTALL = "install", "Install"
    DEMO = "demo", "Demo"

    @classmethod
    def from_value(cls, value: object) -> str:
        """Return the canonical tier value for a string or enum member."""

        if isinstance(value, cls):
            return value.value
        raw = str(value)
        try:
            return cls(raw).value
        except ValueError as error:
            expected = ", ".join(cls.values)
            raise ImproperlyConfigured(f"Unknown resource tier {raw!r}; expected one of {expected}") from error

    @classmethod
    def with_prerequisites(cls, tiers: Iterable[object] | None) -> tuple[str, ...]:
        """Return selected tiers plus the earlier tiers they depend on."""

        if tiers is None:
            return tuple(cls.values)

        selected: set[str] = set()
        values = tuple(cls.values)
        for tier in tiers:
            value = cls.from_value(tier)
            index = values.index(value)
            selected.update(values[: index + 1])
        return tuple(value for value in values if value in selected)
