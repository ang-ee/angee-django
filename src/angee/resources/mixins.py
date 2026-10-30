"""Cooperative model hooks for the resource loader's completed row batches."""

from __future__ import annotations

from collections.abc import Iterable
from typing import TYPE_CHECKING, Any, ClassVar, Self, cast

from django.core import checks
from django.db import models

from angee.resources.tiers import ResourceTier

if TYPE_CHECKING:
    from angee.resources.loader import AngeeResource


class ResourceLoadMixin(models.Model):
    """Terminate the cooperative post-load chain without contributing fields.

    Resource participants do their local work, then call ``super()`` with the
    same batch and options. A skipped local action still delegates; an exception
    aborts the chain and the loader's transaction. Shared abstract ancestry lets
    Python invoke each participant once on the final concrete model.
    """

    resource_class: ClassVar[type[AngeeResource] | None] = None
    """Optional native import adapter, validated by build_resource before loading.

    None uses AngeeResource. Custom adapters and batch lock hooks require this
    mixin, so the loader and its lock preflight consume the same declaration.
    """

    catalogue: ClassVar[bool] = False
    """Whether this source declares catalogue data; read without inheritance."""

    catalogue_tier: ClassVar[str] = str(ResourceTier.MASTER)
    """Default resource tier for this source's catalogue rows."""

    catalogue_tiers: ClassVar[tuple[str, ...] | None] = None
    """Optional allowed tiers for catalogues spanning several resource tiers."""

    class Meta:
        """Keep the hook protocol abstract and tableless."""

        abstract = True

    @classmethod
    def is_catalogue_model(cls) -> bool:
        """Return whether this source declares catalogue data."""

        return bool(cls.__dict__.get("catalogue", False))

    @classmethod
    def get_catalogue_tier(cls) -> str:
        """Return the source-local default tier."""

        return str(cls.__dict__.get("catalogue_tier", ResourceTier.MASTER))

    @classmethod
    def get_catalogue_tiers(cls) -> tuple[str, ...]:
        """Return the source-local allowed tiers, or its single default tier."""

        declared = cls.__dict__.get("catalogue_tiers")
        return (cls.get_catalogue_tier(),) if declared is None else cast(tuple[str, ...], declared)

    @classmethod
    def check(cls, **kwargs: Any) -> list[checks.CheckMessage]:
        """Check resource declarations alongside the model's native checks."""

        errors = super().check(**kwargs)
        if not cls.is_catalogue_model():
            return errors
        default_tier = cls.get_catalogue_tier()
        tiers = cls.get_catalogue_tiers()
        if (
            default_tier in ResourceTier.values
            and isinstance(tiers, tuple)
            and bool(tiers)
            and all(isinstance(tier, str) and tier in ResourceTier.values for tier in tiers)
            and len(set(tiers)) == len(tiers)
            and default_tier in tiers
        ):
            return errors
        expected = ", ".join(repr(value) for value in ResourceTier.values)
        errors.append(
            checks.Error(
                f"{cls._meta.label}.catalogue_tier must be a member of its nonempty, unique "
                f"catalogue_tiers tuple and every tier must be one of {expected}; "
                f"got default {default_tier!r} and tiers {tiers!r}.",
                obj=cls,
                id="angee.E014",
            )
        )
        return errors

    @classmethod
    def after_resource_load(
        cls,
        instances: Iterable[Self],
        *,
        tier: str,
        source: str,
    ) -> None:
        """Finish the post-load chain after every participant has run."""
