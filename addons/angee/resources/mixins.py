"""Cooperative model hooks for the resource loader's completed row batches."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from django.db import models


@dataclass(frozen=True, slots=True)
class ResourceWritePreparation:
    """Model-owned targets to prepare before a resource transaction writes rows."""

    owner: Any
    targets: frozenset[Any]


class ResourceLoadMixin(models.Model):
    """Terminate the cooperative post-load chain without contributing fields.

    Resource participants do their local work, then call ``super()`` with the
    same batch and options. A skipped local action still delegates; an exception
    aborts the chain and the loader's transaction. Shared abstract ancestry lets
    Python invoke each participant once on the final concrete model.
    """

    class Meta:
        """Keep the hook protocol abstract and tableless."""

        abstract = True

    @classmethod
    def resource_write_preparation(cls, resource: Any, dataset: Any) -> ResourceWritePreparation | None:
        """Return optional model-owned write targets needed before importing a batch."""

        del resource, dataset
        return None

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
