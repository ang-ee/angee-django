"""Retrieval backend strategy and the bundled lexical backend.

A :class:`~angee.knowledge.models.Vault` is the search namespace and the
per-namespace selection point: its ``retrieval_class`` field names one of these
by a key in ``ANGEE_KNOWLEDGE_RETRIEVAL_CLASSES``, and the vault's ``retrieval``
property resolves and binds it (through :meth:`~angee.knowledge.models.Vault.retrieval_for`,
the one public resolution seam). Mirrors ``agents.InferenceBackend`` / ``storage.Backend``
— the owning row binds the impl in ``__init__`` and the method
(:meth:`RetrievalBackend.search`) carries the operation arguments; a semantic plugin
(pgvector/graphrag) contributes its own backend key through autoconfig without editing
this addon.

Backends return an **actor-scoped** ``Page`` queryset/list: REBAC row scope is
applied here (``apply_ambient_scope``), so a caller's ambient actor only ever sees
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

    Subclasses search the bound ``vault``'s pages and return an actor-scoped
    ``Page`` queryset/list. Abstract base: it leaves ``key`` blank and stays out
    of the registry; concrete leaves register a key. A future fan-out backend
    (lexical + semantic, fused) is a drop-in subclass — it resolves its arms
    through ``vault.retrieval_for(key)`` and blends their results in :meth:`search`.
    """

    category = "retrieval"
    label = "Retrieval"
    icon = "magnifying-glass"

    def __init__(self, vault: Any) -> None:
        """Bind this backend to the vault whose pages it searches."""

        self.vault = vault

    def search(self, query: str, *, first: int = 20) -> Iterable[Any]:
        """Return the actor-visible pages in this vault matching ``query``."""

        del query, first
        raise NotImplementedError("RetrievalBackend subclasses must implement search().")


class LexicalRetrievalBackend(RetrievalBackend):
    """Default backend: case-insensitive substring match over title and body.

    The registry default. Title and body are matched with ``icontains`` and the
    result is REBAC row-scoped to the ambient actor; the body match is a sequential
    scan (no full-text index — that is a migration the FTS/pgvector plugin owns).
    """

    key = "lexical"
    label = "Lexical"

    def search(self, query: str, *, first: int = 20) -> Iterable[Any]:
        """Return up to ``first`` actor-visible pages whose title or body matches ``query``."""

        page_model = apps.get_model("knowledge", "Page")
        rows = (
            page_model._default_manager.filter(vault=self.vault)
            .filter(Q(title__icontains=query) | Q(markdown__body__icontains=query))
            .order_by("title", "sqid")
            .apply_ambient_scope()
        )
        return rows[:first]
