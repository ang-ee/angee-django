"""Retrieval backend strategy and the bundled lexical backend.

A :class:`~angee.knowledge.models.Vault` is the search namespace and the
per-namespace selection point: its ``retrieval_class`` field names one of these
by a key in ``ANGEE_KNOWLEDGE_RETRIEVAL_CLASSES``. ``VaultQuerySet.search_pages``
groups a bounded set of readable vaults by that field and resolves each backend
class once. :meth:`RetrievalBackend.search_many` searches the whole group; the
vault's bound ``retrieval.search`` delegates to the same operation for one vault.
A semantic plugin contributes its backend key through autoconfig.

Backends return **actor-scoped** :class:`RetrievalHit` values: REBAC row scope is
applied here (``scoped``), so a caller's ambient actor only ever sees
pages it may read. This module stays free of the markdown text owners — search is
ORM filtering over the existing ``title``/``body`` columns, not parsing.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from itertools import islice
from typing import Any

from django.apps import apps
from django.db.models import Q

from angee.base.implementations import ImplBase

MAX_SEARCH_PAGE_SIZE = 100


@dataclass(frozen=True, kw_only=True)
class RetrievalHit:
    """One readable page and its best evidence, ranked within a search result.

    ``start`` and ``end`` are Python character offsets into the current markdown
    body, with an exclusive end. Page-only backends leave the passage fields
    empty. ``rank`` is one-based; ``score`` is backend-local, never compared
    across strategies. The page remains the canonical public identity.
    """

    page: Any
    rank: int
    heading_path: tuple[str, ...] = ()
    start: int | None = None
    end: int | None = None
    text: str | None = None
    score: float | None = None


class RetrievalBackend(ImplBase):
    """The search strategy one vault resolves to.

    Subclasses implement ``search_many`` over vaults selecting this strategy and
    return actor-scoped hits. A bound vault composes that operation through
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

    def search(self, query: str, *, first: int = 20) -> Iterable[RetrievalHit]:
        """Search this vault within the shared page budget, even for plugin callers."""

        limit = max(0, min(first, MAX_SEARCH_PAGE_SIZE))
        if not limit:
            return ()
        return islice(type(self).search_many((self.vault,), query, first=limit), limit)

    @classmethod
    def search_many(cls, vaults: Iterable[Any], query: str, *, first: int = 20) -> Iterable[RetrievalHit]:
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
    def search_many(cls, vaults: Iterable[Any], query: str, *, first: int = 20) -> Iterable[RetrievalHit]:
        """One lexical scan over a bounded group, retaining page and trash scopes."""

        page_model = apps.get_model("knowledge", "Page")
        rows = (
            page_model._default_manager.filter(vault__in=vaults)
            .untrashed()
            .filter(Q(title__icontains=query) | Q(markdown__body__icontains=query))
            .order_by("title", "sqid")
            .scoped()
        )
        return [
            RetrievalHit(page=page, rank=rank)
            for rank, page in enumerate(rows[: max(0, min(first, MAX_SEARCH_PAGE_SIZE))], 1)
        ]
