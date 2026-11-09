"""Settings fragments contributed by the knowledge pgvector plugin."""

from __future__ import annotations

from angee.knowledge_graph_pgvector.constants import RECONCILE_SECONDS, RECONCILE_TASK

SETTINGS = {
    # Contribute the ``pgvector`` retrieval backend into the knowledge registry
    # via a dotted-key deep-merge (mirrors ``agents_integrate_anthropic``). The
    # composer folds this one entry into ``ANGEE_KNOWLEDGE_RETRIEVAL_CLASSES``
    # without editing the knowledge addon; a ``Vault`` selects it with
    # ``retrieval_class = "pgvector"``.
    "ANGEE_KNOWLEDGE_RETRIEVAL_CLASSES.pgvector": ("angee.knowledge_graph_pgvector.retrieval.PgvectorRetrievalBackend"),
    "CELERY_BEAT_SCHEDULE:append": {
        RECONCILE_TASK: {"task": RECONCILE_TASK, "schedule": float(RECONCILE_SECONDS)},
    },
}
"""Django settings contributed when the knowledge pgvector plugin is installed."""
