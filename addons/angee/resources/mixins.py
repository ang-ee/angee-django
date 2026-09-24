"""Cooperative model hooks for the resource loader's completed row batches."""

from __future__ import annotations

from collections.abc import Iterable
from typing import TYPE_CHECKING, ClassVar

from django.db import models

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

    class Meta:
        """Keep the hook protocol abstract and tableless."""

        abstract = True

    @classmethod
    def after_resource_load(
        cls,
        instances: Iterable[models.Model],
        *,
        tier: str,
        source: str,
        publish: bool = False,
    ) -> None:
        """Finish the post-load chain after every participant has run."""
