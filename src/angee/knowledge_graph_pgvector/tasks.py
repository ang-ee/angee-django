"""Jobs dispatch to index owners; provider retries are explicitly classified."""

from celery import shared_task
from django.apps import apps

from angee.base.locks import LockKey, task_lock
from angee.knowledge_graph_pgvector.constants import EMBED_PAGE_TASK, EXTRACT_PASSAGE_TASK, RECONCILE_TASK
from angee.knowledge_graph_pgvector.extraction import TransientGraphError
from angee.knowledge_graph_pgvector.managers import TransientEmbeddingError


@shared_task(
    name=EMBED_PAGE_TASK,
    autoretry_for=(TransientEmbeddingError,),
    retry_backoff=True,
    retry_jitter=True,
    retry_kwargs={"max_retries": 3},
)
def embed_page(pk: int) -> int:
    """Suppress duplicate provider calls; the manager fences all persisted writes."""

    with task_lock(LockKey("knowledge_graph_pgvector", ("embed_page", str(pk)))) as acquired:
        if not acquired:
            return 0
        return apps.get_model("knowledge_graph_pgvector", "PassageEmbedding").objects.embed_page(pk)


@shared_task(
    name=EXTRACT_PASSAGE_TASK,
    autoretry_for=(TransientGraphError,),
    retry_backoff=True,
    retry_jitter=True,
    retry_kwargs={"max_retries": 3},
)
def extract_passage(pk: int) -> int:
    """Only the reconciler schedules idle passages; the owner rechecks that gate."""

    with task_lock(LockKey("knowledge_graph_pgvector", ("extract_passage", str(pk)))) as acquired:
        if not acquired:
            return 0
        return apps.get_model("knowledge_graph_pgvector", "Passage").objects.extract_passage(pk)


@shared_task(name=RECONCILE_TASK)
def reconcile_embeddings(timestamp: int | None = None, *, vault_id: int | None = None) -> int:
    """Reconcile embeddings, idle graph extractions and mention-less entities."""

    with task_lock(LockKey("knowledge_graph_pgvector", ("reconcile", str(vault_id)))) as acquired:
        if not acquired:
            return 0
        return apps.get_model("knowledge_graph_pgvector", "Passage").objects.reconcile(vault_id=vault_id)
