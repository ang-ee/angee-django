"""Prepare PostgreSQL before host migrations and wire index lifecycle hooks."""

from django.apps import AppConfig
from django.db.models.signals import pre_migrate


class KnowledgeGraphPgvectorConfig(AppConfig):
    name = "angee.knowledge_graph_pgvector"
    default = True

    def ready(self) -> None:
        super().ready()
        from angee.knowledge_graph_pgvector import signals

        signals.connect()
        pre_migrate.connect(
            signals.prepare_vector_extension,
            sender=self,
            weak=False,
            dispatch_uid="angee.knowledge_graph_pgvector.extension",
        )
