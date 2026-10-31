"""Chainable mount source admission scopes."""

from __future__ import annotations

from collections.abc import Iterable
from typing import TYPE_CHECKING, Self

from angee.integrate.querysets import IntegrationQuerySet

if TYPE_CHECKING:
    from angee.storage_integrate.models import Mount


class MountQuerySet[MountT: Mount](IntegrationQuerySet[MountT]):
    """Select persisted mount identities independently of source transport."""

    def for_roots(self, roots: Iterable[str]) -> Self:
        """Roots collide globally across mount modes and local-source implementations."""

        return self.filter(config__root__in=tuple(roots))
