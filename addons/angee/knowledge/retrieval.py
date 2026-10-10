"""Retrieval backend strategy and the bundled lexical backend.

A :class:`~angee.knowledge.models.Vault` is the search namespace and the
per-namespace selection point: its ``retrieval_class`` field names one of these
by a key in ``ANGEE_KNOWLEDGE_RETRIEVAL_CLASSES``. ``VaultQuerySet.search_pages``
groups a bounded set of readable vaults by that field and resolves each backend
class once. :meth:`RetrievalBackend.search_many` searches the whole group; the
vault's bound ``retrieval.search`` delegates to the same operation for one vault.
A semantic plugin contributes its backend key through autoconfig.

Backends return an **actor-scoped** ``Page`` queryset/list: REBAC row scope is
applied here (``scoped``), so a caller's ambient actor only ever sees
pages it may read. This module stays free of the markdown text owners — search is
ORM filtering over the existing ``title``/``body`` columns, not parsing.
"""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from django.apps import apps
from django.db.models import Q

from angee.base.impl import ImplBase


class RetrievalBackend(ImplBase):
    """The search strategy one vault resolves to.

    Subclasses implement ``search_many`` over vaults selecting this strategy and
    return actor-scoped pages. A bound vault composes that operation through
    ``search``. Concrete leaves register a key; the abstract base stays out of
    the registry.
    """
    registry_setting = "ANGEE_KNOWLEDGE_RETRIEVAL_CLASSES"

    category = "retrieval"
    label = "Retrieval"
    icon = "magnifying-glass"

    def __init__(self, vault: Any) -> None:
        """Bind this backend to the vault whose pages it searches."""

        self.vault = vault

    def search(self, query: str, *, first: int = 20) -> Iterable[Any]:
        """Compose the group search for this one bound vault."""
        return type(self).search_many((self.vault,), query, first=first)

    @classmethod
    def search_many(cls, vaults: Iterable[Any], query: str, *, first: int = 20) -> Iterable[Any]:
        """Search one same-strategy vault group with one actor-scoped operation."""
        raise NotImplementedError("Retrieval backends must implement search_many().")


class LexicalRetrievalBackend(RetrievalBackend):
    """Default backend: case-insensitive substring match over title and body.

    The registry default. Title and body are matched with ``icontains`` and the
    result is REBAC row-scoped to the ambient actor; the body match is a sequential
    scan (no full-text index — that is a migration the FTS/pgvector plugin owns).
    """

    key = "lexical"
    label = "Lexical"

    @classmethod
    def search_many(cls, vaults: Iterable[Any], query: str, *, first: int = 20) -> Iterable[Any]:
        """One lexical scan over a bounded group, retaining page and trash scopes."""

        page_model = apps.get_model("knowledge", "Page")
        rows = (
            page_model._default_manager.filter(vault__in=vaults)
            .untrashed()
            .filter(Q(title__icontains=query) | Q(markdown__body__icontains=query))
            .order_by("title", "sqid")
            .scoped()
        )
        return rows[:first]
