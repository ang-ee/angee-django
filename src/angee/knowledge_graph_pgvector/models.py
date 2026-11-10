"""Passage evidence, per-model vectors and vault-local graph identities."""

from __future__ import annotations

from datetime import timedelta
from typing import Any, ClassVar, Self, cast

from django.apps import apps
from django.contrib.postgres.indexes import GinIndex
from django.contrib.postgres.search import SearchVectorField
from django.core.exceptions import ValidationError
from django.db import models, transaction
from django.db.models.functions import JSONObject
from django.utils import timezone
from django_compose.models import Composable
from pgvector.django import VectorField
from pydantic import ValidationError as OutputValidationError
from pydantic_ai.exceptions import UnexpectedModelBehavior
from rebac import current_actor, system_context

from angee.agents.models import InferenceModelUse, InferenceOutputError
from angee.base.fields.text import DiagnosticTextField
from angee.base.models.base import AngeeDataModel
from angee.base.querysets import AngeeQuerySet
from angee.base.validators import validate_value
from angee.integrate.errors import doubling_backoff
from angee.knowledge.retrieval import RetrievalHit
from angee.knowledge_graph_pgvector.constants import EMBEDDING_ROLE
from angee.knowledge_graph_pgvector.extraction import EXTRACTION_ROLE, Kind
from angee.knowledge_graph_pgvector.managers import EntityManager, PassageEmbeddingManager, PassageManager
from angee.knowledge_graph_pgvector.options import default_entity_kinds
from angee.knowledge_graph_pgvector.retrieval import PgvectorRetrievalBackend


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
        if self.embedding_model_id is not None and (
            self._state.adding or not type(self).objects.filter(
                pk=self.pk, embedding_model_id=self.embedding_model_id,
            ).exists()
        ):
            actor = current_actor()
            with system_context(reason="knowledge_graph_pgvector.vault.model_admission"):
                model = apps.get_model("agents", "InferenceModel").objects.get(pk=self.embedding_model_id)
            model.require_usable(
                actor,
                EMBEDDING_ROLE,
                uses={InferenceModelUse.EMBEDDING},
            )


class VaultGraphExtraction(Composable, extends="knowledge.Vault"):
    """Selecting a CHAT deployment opts this vault into passage graph extraction."""

    extraction_model = models.ForeignKey(
        "agents.InferenceModel",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="graph_vaults",
        limit_choices_to={"model_use": InferenceModelUse.CHAT},
    )
    graph_entity_kinds = models.JSONField(default=default_entity_kinds)
    hasura_filterable_fields: ClassVar[tuple[str, ...]] = ("extraction_model",)
    hasura_insertable_fields: ClassVar[tuple[str, ...]] = ("extraction_model", "graph_entity_kinds")
    hasura_updatable_fields: ClassVar[tuple[str, ...]] = ("extraction_model", "graph_entity_kinds")

    class Meta:
        abstract = True

    def clean(self) -> None:
        super().clean()
        kinds = validate_value(list[Kind], self.graph_entity_kinds, field="graph_entity_kinds")
        if not 1 <= len(kinds) <= 20 or len(set(kinds)) != len(kinds):
            raise ValidationError({"graph_entity_kinds": "Declare 1 to 20 distinct entity kinds."})
        self.graph_entity_kinds = sorted(kinds)
        if self.extraction_model_id is not None and self.retrieval_class != PgvectorRetrievalBackend.key:
            raise ValidationError({"extraction_model": "Graph extraction requires the pgvector retrieval backend."})
        if self.extraction_model_id is not None and (
            self._state.adding or not type(self).objects.filter(
                pk=self.pk, extraction_model_id=self.extraction_model_id,
            ).exists()
        ):
            actor = current_actor()
            with system_context(reason="knowledge_graph_pgvector.vault.extraction_admission"):
                model = apps.get_model("agents", "InferenceModel").objects.get(pk=self.extraction_model_id)
            model.require_usable(actor, EXTRACTION_ROLE, uses={InferenceModelUse.CHAT})

    def save(self, *args: Any, **kwargs: Any) -> None:
        kinds = sorted(self.graph_entity_kinds)
        if kinds != self.graph_entity_kinds and kwargs.get("update_fields") is not None:
            kwargs["update_fields"] = set(kwargs["update_fields"]) | {"graph_entity_kinds"}
        self.graph_entity_kinds = kinds
        super().save(*args, **kwargs)


class IndexState(models.Model):
    """Failure state shared by the owning page index and passage graph work.

    The exact input snapshot invalidates a failure when source or selection
    changes. Invalid output completes that input with an error; other failures
    wait with doubling backoff, capped at one day.
    """

    index_failure = models.JSONField(default=dict, blank=True, editable=False)
    index_attempts = models.PositiveIntegerField(default=0, editable=False)
    index_retry_at = models.DateTimeField(null=True, blank=True, editable=False)
    index_error = DiagnosticTextField(blank=True, default="")

    class Meta:
        abstract = True

    @staticmethod
    def index_due(**snapshot: Any) -> models.Q:
        return (
            models.Q(index_attempts=0) | ~models.Q(index_failure=JSONObject(**snapshot))
            | models.Q(index_retry_at__lte=timezone.now())
        )

    @staticmethod
    def cleared_index_failure() -> dict[str, Any]:
        return {"index_failure": {}, "index_attempts": 0, "index_retry_at": None, "index_error": ""}

    def record_index_failure(self, snapshot: dict[str, Any], error: Exception) -> None:
        """Retain only failure type and input identity, never provider text or secrets."""

        with transaction.atomic():
            rows = cast(AngeeQuerySet[Self], type(self)._default_manager.get_queryset()).filter(pk=self.pk)
            row = rows.lock_if_supported(no_key=True).first()
            if row is None:
                return
            attempts = row.index_attempts + 1 if row.index_failure == snapshot else 1
            invalid_output = isinstance(error, (OutputValidationError, InferenceOutputError, UnexpectedModelBehavior))
            retry_at = None if invalid_output else timezone.now() + doubling_backoff(attempts, cap=timedelta(days=1))
            rows.update(
                index_failure=snapshot, index_attempts=attempts, index_retry_at=retry_at,
                index_error=type(error).__name__,
            )


class PageIndexState(IndexState, Composable, extends="knowledge.Page"):
    """The page owns index failures even before any passage can be built."""

    class Meta:
        abstract = True


class Passage(IndexState, AngeeDataModel, concrete=True):
    """Current body coordinates; unchanged text retains its evidence identity.

    Offsets are Python character indices into the original body, including CRLF.
    Source metadata comes from knowledge; ``text_hash`` identifies the embedding
    input (title, heading path and slice). A vault move invalidates old graph evidence.
    """

    sqid_prefix = "psg_"
    page = models.ForeignKey("knowledge.Page", on_delete=models.CASCADE, related_name="passages")
    ordinal = models.PositiveIntegerField()
    heading_path = models.JSONField(default=list, blank=True)
    start = models.PositiveIntegerField()
    end = models.PositiveIntegerField()
    text_hash = models.CharField(max_length=64)
    source_body_hash = models.CharField(max_length=64)
    source_title = models.TextField()
    source_vault_id = models.BigIntegerField()
    search = SearchVectorField(null=True, editable=False)
    graph_model = models.ForeignKey(
        "agents.InferenceModel",
        null=True,
        blank=True,
        editable=False,
        on_delete=models.SET_NULL,
        related_name="extracted_passages",
    )
    graph_kinds = models.JSONField(default=list, blank=True, editable=False)
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

    def matches_source(self, markdown: Any) -> bool:
        """Compare the canonical metadata, without rehashing the body."""

        return (self.page_id, self.source_body_hash, self.source_title, self.source_vault_id) == (
            markdown.pk, markdown.body_hash, markdown.title, markdown.vault_id,
        )

    def text(self, markdown: Any) -> str:
        """Read the canonical slice only from the source revision it describes."""

        if not self.matches_source(markdown):
            raise ValueError("The passage is stale; its source has changed.")
        return markdown.body[self.start : self.end] or markdown.title

    def embedding_text(self, markdown: Any) -> str:
        """The same contextual text owns the content hash and provider input."""

        prefix = " › ".join((markdown.title, *self.heading_path))
        return f"{prefix}\n{markdown.body[self.start : self.end]}".strip()

    def to_hit(self, page: Any, markdown: Any, *, rank: int) -> RetrievalHit:
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


class Entity(AngeeDataModel, concrete=True):
    """A vault-local identity; only readable mentions expose its name. No summaries."""

    sqid_prefix = "kge_"
    vault = models.ForeignKey("knowledge.Vault", on_delete=models.CASCADE, related_name="graph_entities")
    key = models.CharField(max_length=200)
    kind = models.CharField(max_length=64)
    objects = EntityManager()

    class Meta:
        abstract = True
        verbose_name_plural = "entities"
        ordering = ("key", "kind", "pk")
        rebac_resource_type = "knowledge_graph_pgvector/entity"
        constraints = (models.UniqueConstraint(fields=("vault", "key", "kind"), name="pgv_entity_vault_key_kind"),)
        indexes = (GinIndex(fields=("key",), opclasses=("gin_trgm_ops",), name="pgv_entity_key_trgm"),)


class EntityMention(AngeeDataModel, concrete=True):
    """One passage's evidence about an entity; never a cross-page description."""

    sqid_prefix = "kgm_"
    passage = models.ForeignKey(
        "knowledge_graph_pgvector.Passage", on_delete=models.CASCADE, related_name="entity_mentions"
    )
    entity = models.ForeignKey("knowledge_graph_pgvector.Entity", on_delete=models.CASCADE, related_name="mentions")
    name = models.CharField(max_length=200)
    description = models.TextField(blank=True)

    class Meta:
        abstract = True
        rebac_resource_type = "knowledge_graph_pgvector/entity_mention"
        constraints = (models.UniqueConstraint(fields=("passage", "entity"), name="pgv_mention_passage_entity"),)



class EntityEdge(AngeeDataModel, concrete=True):
    """One supporting passage per relationship; weights combine only at query time."""

    sqid_prefix = "kgr_"
    passage = models.ForeignKey(
        "knowledge_graph_pgvector.Passage", on_delete=models.CASCADE, related_name="entity_edges"
    )
    subject = models.ForeignKey(
        "knowledge_graph_pgvector.Entity", on_delete=models.CASCADE, related_name="outgoing_edges"
    )
    predicate = models.CharField(max_length=128)
    object = models.ForeignKey(
        "knowledge_graph_pgvector.Entity", on_delete=models.CASCADE, related_name="incoming_edges"
    )
    description = models.TextField(blank=True)
    weight = models.FloatField(default=1)

    class Meta:
        abstract = True
        rebac_resource_type = "knowledge_graph_pgvector/entity_edge"
        constraints = (
            models.UniqueConstraint(fields=("passage", "subject", "predicate", "object"), name="pgv_edge_evidence"),
            models.CheckConstraint(condition=models.Q(weight__gt=0, weight__lte=1), name="pgv_edge_weight"),
            models.CheckConstraint(condition=~models.Q(subject=models.F("object")), name="pgv_edge_distinct_ends"),
        )
