"""Native migration preparation and commit-deferred passage indexing."""

from collections.abc import Iterable
from typing import Any

from django.apps import apps
from django.core.exceptions import ImproperlyConfigured
from django.db import DatabaseError, connections
from django.db.models.signals import post_save
from rebac import system_context

from angee.base.signals import connect_for_models
from angee.jobs.enqueue import enqueue_task
from angee.knowledge_graph_pgvector.constants import EMBED_PAGE_TASK, RECONCILE_SECONDS, RECONCILE_TASK
from angee.knowledge_graph_pgvector.retrieval import PgvectorRetrievalBackend


def prepare_vector_extension(sender: Any, using: str, **kwargs: Any) -> None:
    """Prepare vector storage and trigram resolution before host migrations.

    This native pre_migrate receiver is the single extension owner.
    A preinstalled extension needs no elevated role; a missing extension needs
    the server package and creation privileges. SQLite remains a no-op here.
    """

    connection = connections[using]
    if connection.vendor != "postgresql":
        return
    for extension in ("vector", "pg_trgm"):
        try:
            with connection.cursor() as cursor:
                cursor.execute(f"CREATE EXTENSION IF NOT EXISTS {connection.ops.quote_name(extension)}")
        except DatabaseError as error:
            raise ImproperlyConfigured(
                f"Cannot prepare PostgreSQL extension '{extension}'. Install the extension on the server and grant "
                f"extension-creation privileges, or have an administrator CREATE EXTENSION {extension} before migrate."
            ) from error


def connect() -> None:
    """Subscribe once, including concrete source models registered by test hosts."""

    connect_for_models(
        post_save,
        enqueue_embedding,
        applies=lambda model: (
            model._meta.label_lower
            in {
                "knowledge.page",
                "knowledge.markdownpage",
                "knowledge.vault",
            }
        ),
        dispatch_uid="angee.knowledge_graph_pgvector.index",
    )


def enqueue_embedding(
    sender: type[Any],
    instance: Any,
    raw: bool = False,
    update_fields: Iterable[str] | None = None,
    **kwargs: Any,
) -> None:
    """Signals dispatch identifiers only; managers decide whether work is needed."""

    if raw:
        return
    vault_id = instance.pk if sender._meta.label_lower == "knowledge.vault" else instance.vault_id
    with system_context(reason="knowledge_graph_pgvector.enqueue_selection"):
        selected = apps.get_model("knowledge", "Vault").objects.filter(
            pk=vault_id, retrieval_class=PgvectorRetrievalBackend.key,
        ).exists()
    if not selected:
        return
    if sender._meta.label_lower == "knowledge.vault":
        if update_fields is None or {
            "retrieval_class",
            "embedding_model",
            "embedding_model_id",
            "extraction_model",
            "extraction_model_id",
            "graph_entity_kinds",
        }.intersection(update_fields):
            enqueue_task(RECONCILE_TASK, kwargs={"vault_id": instance.pk}, expires=RECONCILE_SECONDS, robust=True)
    elif update_fields is None or {"body", "title", "vault", "vault_id"}.intersection(update_fields):
        enqueue_task(EMBED_PAGE_TASK, kwargs={"pk": instance.pk}, expires=RECONCILE_SECONDS, robust=True)
