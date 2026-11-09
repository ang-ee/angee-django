"""Passage evidence and per-model vectors derived from knowledge's markdown."""

from __future__ import annotations

from typing import Any, ClassVar

from django.apps import apps
from django.contrib.postgres.indexes import GinIndex
from django.contrib.postgres.search import SearchVectorField
from django.core.exceptions import ImproperlyConfigured, ValidationError
from django.db import models
from django_compose.models import Composable
from pgvector.django import VectorField
from rebac import current_actor, system_context

from angee.agents.models import InferenceModelUse
from angee.base.models.base import AngeeDataModel
from angee.knowledge.retrieval import RetrievalHit
from angee.knowledge_graph_pgvector.checks import require_pgvector
from angee.knowledge_graph_pgvector.managers import PassageEmbeddingManager, PassageManager


class VaultEmbeddingModel(Composable, extends="knowledge.Vault"):
    """Opt a vault into document egress to one approved embedding deployment."""

    embedding_model = models.ForeignKey(
        "agents.InferenceModel",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="embedding_vaults",
        limit_choices_to={"model_use": InferenceModelUse.EMBEDDING},
    )
    hasura_filterable_fields: ClassVar[tuple[str, ...]] = ("embedding_model",)
    hasura_insertable_fields: ClassVar[tuple[str, ...]] = ("embedding_model",)
    hasura_updatable_fields: ClassVar[tuple[str, ...]] = ("embedding_model",)

    class Meta:
        abstract = True

    def clean(self) -> None:
        """Admit the selected deployment as the writer and reject SQLite selection."""

        super().clean()
        if self.retrieval_class == "pgvector":
            try:
                require_pgvector()
            except ImproperlyConfigured as error:
                raise ValidationError({"retrieval_class": str(error)}) from error
        if self.embedding_model_id is not None:
            actor = current_actor()
            with system_context(reason="knowledge_graph_pgvector.vault.model_admission"):
                model = apps.get_model("agents", "InferenceModel").objects.get(pk=self.embedding_model_id)
            model.require_usable(
                actor,
                "knowledge_embedding",
                uses={InferenceModelUse.EMBEDDING},
            )


class Passage(AngeeDataModel, concrete=True):
    """Current body coordinates; unchanged text retains its evidence identity.

    Offsets are Python character indices into the original body, including CRLF.
    ``source_hash`` fingerprints title and body, while ``text_hash`` fingerprints
    the embedding input (title, heading path and slice). Neither stores prose.
    """

    sqid_prefix = "psg_"
    page = models.ForeignKey("knowledge.Page", on_delete=models.CASCADE, related_name="passages")
    ordinal = models.PositiveIntegerField()
    heading_path = models.JSONField(default=list, blank=True)
    start = models.PositiveIntegerField()
    end = models.PositiveIntegerField()
    text_hash = models.CharField(max_length=64)
    source_hash = models.CharField(max_length=64)
    search = SearchVectorField(null=True, editable=False)
    objects = PassageManager()

    class Meta:
        abstract = True
        ordering = ("page_id", "ordinal")
        rebac_resource_type = "knowledge_graph_pgvector/passage"
        constraints = (
            models.UniqueConstraint(fields=("page", "ordinal"), name="pgv_passage_page_ordinal"),
            models.CheckConstraint(condition=models.Q(end__gte=models.F("start")), name="pgv_passage_offsets"),
        )
        indexes = (GinIndex(fields=("search",), name="pgv_passage_search"),)

    def text(self, markdown: Any) -> str:
        """Read the canonical slice only from the source revision it describes."""

        if markdown.pk != self.page_id or self.source_hash != type(self).objects.fingerprint(markdown):
            raise ValueError("The passage is stale; its source has changed.")
        return markdown.body[self.start : self.end] or markdown.title

    def embedding_text(self, markdown: Any) -> str:
        """The same contextual text owns the content hash and provider input."""

        prefix = " › ".join((markdown.title, *self.heading_path))
        return f"{prefix}\n{markdown.body[self.start : self.end]}".strip()

    def hit(self, page: Any, markdown: Any, *, rank: int) -> RetrievalHit:
        """Project current evidence after the caller has scoped both source rows."""

        return RetrievalHit(
            page=page,
            rank=rank,
            heading_path=tuple(self.heading_path),
            start=self.start,
            end=self.end,
            text=self.text(markdown),
        )


class PassageEmbedding(AngeeDataModel, concrete=True):
    """One vector space per inference model; exact cosine search needs no ANN index."""

    sqid_prefix = "pse_"
    passage = models.ForeignKey("knowledge_graph_pgvector.Passage", on_delete=models.CASCADE, related_name="embeddings")
    model = models.ForeignKey("agents.InferenceModel", on_delete=models.CASCADE, related_name="passage_embeddings")
    embedding = VectorField()
    objects = PassageEmbeddingManager()

    class Meta:
        abstract = True
        rebac_resource_type = "knowledge_graph_pgvector/passage_embedding"
        constraints = (models.UniqueConstraint(fields=("passage", "model"), name="pgv_embedding_passage_model"),)
        indexes = (models.Index(fields=("model", "passage"), name="pgv_embedding_model_passage"),)
