"""Source models owned by the base addon."""

from __future__ import annotations

from django.core.exceptions import ImproperlyConfigured
from django.db import models

from angee.base.managers import ResourceManager
from angee.base.mixins import AngeeModel


class Resource(AngeeModel):
    """Ledger row for idempotent resource imports."""

    class Tier(models.TextChoices):
        """Resource file tiers persisted on ledger rows."""

        MASTER = "master", "Master"
        INSTALL = "install", "Install"
        DEMO = "demo", "Demo"

        @classmethod
        def from_value(cls, value: object) -> str:
            """Return a tier value from TextChoices or string shorthand."""

            if isinstance(value, cls):
                return value.value
            raw = str(value)
            try:
                return cls(raw).value
            except ValueError as exc:
                expected = ", ".join(choice.value for choice in cls)
                raise ImproperlyConfigured(
                    f"Unknown resource tier {raw!r}; "
                    f"expected one of {expected}"
                ) from exc

        @classmethod
        def values(cls) -> tuple[str, ...]:
            """Return resource tier values in load order."""

            return tuple(choice.value for choice in cls)

    source_addon = models.CharField(max_length=200)
    source_path = models.CharField(max_length=300)
    tier = models.CharField(max_length=40, choices=Tier.choices)
    xref = models.CharField(max_length=160, blank=True, default="")
    content_hash = models.CharField(max_length=64)
    target_model = models.CharField(max_length=120)
    target_id = models.CharField(max_length=120, blank=True, default="")
    loaded_at = models.DateTimeField(auto_now=True)

    objects = ResourceManager()

    class Meta:
        """Django model options."""

        abstract = True
        ordering = ("source_addon", "source_path", "xref", "target_model")
        constraints = (
            models.UniqueConstraint(
                fields=("source_addon", "source_path", "xref", "target_model"),
                name="base_resource_source_target",
            ),
        )
