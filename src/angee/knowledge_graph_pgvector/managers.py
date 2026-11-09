"""Incremental passage indexing and fenced embedding writes."""

from __future__ import annotations

import hashlib
import math
import re
from collections import defaultdict, deque
from itertools import batched
from typing import Any, Self

from django.apps import apps
from django.db import models, transaction
from django.db.models.functions import Cast, Concat, Length
from rebac import system_context
from semantic_text_splitter import MarkdownSplitter

from angee.agents.models import InferenceModelUse
from angee.base.expressions import TextSHA256
from angee.base.managers import AngeeManager
from angee.base.querysets import AngeeQuerySet
from angee.base.search import stamp_search_vector
from angee.jobs.enqueue import enqueue_task
from angee.knowledge_graph_pgvector.checks import require_pgvector
from angee.knowledge_graph_pgvector.constants import EMBED_PAGE_TASK, RECONCILE_BATCH, RECONCILE_SECONDS


class PassageQuerySet(AngeeQuerySet):
    """Read only evidence whose offsets still describe the current page body."""

    def current(self) -> Self:
        return self.filter(source_hash=PassageManager.fingerprint_expression("page__title", "page__markdown__body"))


_PassageManager = AngeeManager.from_queryset(PassageQuerySet)


class PassageManager(_PassageManager):
    """Own chunking, identity-preserving diffs and bounded reconciliation."""

    @staticmethod
    def fingerprint(markdown: Any) -> str:
        """Length-prefix the title so newlines cannot collide with body boundaries."""

        return hashlib.sha256(f"{len(markdown.title)}:{markdown.title}{markdown.body}".encode("utf-8")).hexdigest()

    @staticmethod
    def fingerprint_expression(title: Any, body: Any) -> TextSHA256:
        """The database spelling of the same title/body fingerprint."""

        return TextSHA256(
            Concat(
                Cast(Length(title), models.TextField()),
                models.Value(":"),
                title,
                body,
                output_field=models.TextField(),
            )
        )

    def passages(self, markdown: Any, *, max_input_tokens: int | None = None) -> list[Any]:
        """Split outline sections with native MarkdownSplitter character offsets.

        Without a tokenizer in the model contract, UTF-8 bytes conservatively
        budget input tokens, including the contextual prefix. The default is
        2,000 bytes. A model change retains existing passages and their evidence.
        """

        budget = min(2000, max_input_tokens) if max_input_tokens else 2000
        # Markdown's line coordinates normalize CR/LF, not Unicode separators.
        offsets = [0, *(match.end() for match in re.finditer(r"\r\n?|\n", markdown.body))]
        outline = markdown.parse_outline(markdown.body)
        sections: list[tuple[int, int, tuple[str, ...]]] = []
        ancestry = []
        if not outline or outline[0].line:
            sections.append((0, offsets[outline[0].line] if outline else len(markdown.body), ()))
        for index, entry in enumerate(outline):
            while ancestry and ancestry[-1].level >= entry.level:
                ancestry.pop()
            ancestry.append(entry)
            end = offsets[outline[index + 1].line] if index + 1 < len(outline) else len(markdown.body)
            sections.append((offsets[entry.line], end, tuple(ancestor.text for ancestor in ancestry)))
        rows = []
        source_hash = self.fingerprint(markdown)
        for start, end, heading_path in sections:
            prefix = " › ".join((markdown.title, *heading_path))
            capacity = budget - len((prefix + "\n").encode("utf-8"))
            if capacity < 4:
                raise ValueError("The embedding input budget cannot hold this page's title and heading path.")
            splitter = MarkdownSplitter.from_callback(
                lambda text: len(text.encode("utf-8")),
                (max(1, capacity // 2), capacity),
                overlap=min(100, capacity // 8),
            )
            for offset, chunk in splitter.chunk_indices(markdown.body[start:end]):
                row = self.model(
                    page_id=markdown.pk,
                    ordinal=len(rows),
                    heading_path=list(heading_path),
                    start=start + offset,
                    end=start + offset + len(chunk),
                    source_hash=source_hash,
                )
                row.text_hash = hashlib.sha256(row.embedding_text(markdown).encode("utf-8")).hexdigest()
                rows.append(row)
        if not rows:
            row = self.model(page_id=markdown.pk, ordinal=0, heading_path=[], start=0, end=0, source_hash=source_hash)
            row.text_hash = hashlib.sha256(row.embedding_text(markdown).encode("utf-8")).hexdigest()
            rows.append(row)
        return rows

    def rebuild_for(self, markdown: Any, *, max_input_tokens: int | None = None) -> list[Any]:
        """Diff one snapshot under the page lock; stale writers leave no changes.

        Match repeated hashes by their old ordinal. Move old ordinals out of the
        new range before assigning survivors, so reordering preserves the unique
        constraint and every surviving vector or graph-evidence FK.
        """

        page_model = apps.get_model("knowledge", "Page")
        markdown_model = apps.get_model("knowledge", "MarkdownPage")
        source_hash = self.fingerprint(markdown)
        with system_context(reason="knowledge_graph_pgvector.passages"), transaction.atomic():
            if page_model.objects.filter(pk=markdown.pk).lock_if_supported().first() is None:
                return []
            current = markdown_model.objects.filter(pk=markdown.pk).lock_if_supported().first()
            if current is None or self.fingerprint(current) != source_hash:
                return []
            existing = list(self.filter(page_id=markdown.pk).order_by("ordinal"))
            if existing and all(row.source_hash == source_hash for row in existing):
                return existing
            desired = self.passages(current, max_input_tokens=max_input_tokens)
            by_hash: dict[str, deque[Any]] = defaultdict(deque)
            for row in existing:
                by_hash[row.text_hash].append(row)
            shift = max((row.ordinal for row in existing), default=0) + len(desired) + 1
            self.filter(page_id=markdown.pk).update(ordinal=models.F("ordinal") + shift)
            retained = []
            for candidate in desired:
                if by_hash[candidate.text_hash]:
                    row = by_hash[candidate.text_hash].popleft()
                    for field in ("ordinal", "start", "end", "heading_path", "source_hash"):
                        setattr(row, field, getattr(candidate, field))
                    row.save(update_fields=("ordinal", "start", "end", "heading_path", "source_hash"))
                else:
                    candidate.save(force_insert=True)
                    row = candidate
                    stamp_search_vector(self.filter(pk=row.pk), row.embedding_text(current))
                retained.append(row)
            self.filter(page_id=markdown.pk).exclude(pk__in=[row.pk for row in retained]).delete()
            return retained

    def stale_pages(self, *, vault_id: int | None = None) -> Any:
        """Find missed saves, raw clones, bulk edits and current-model backfills."""

        markdown_model = apps.get_model("knowledge", "MarkdownPage")
        embeddings = apps.get_model("knowledge_graph_pgvector", "PassageEmbedding")
        fresh = self.filter(
            page_id=models.OuterRef("pk"),
            source_hash=self.fingerprint_expression(models.OuterRef("title"), models.OuterRef("body")),
        )
        missing = self.filter(page_id=models.OuterRef("pk")).exclude(
            models.Exists(
                embeddings.objects.filter(
                    passage_id=models.OuterRef("pk"),
                    model_id=models.OuterRef("page__vault__embedding_model_id"),
                )
            )
        )
        pages = (
            markdown_model.objects.filter(
                vault__retrieval_class="pgvector",
                vault__is_trashed=False,
            )
            .untrashed()
            .filter(~models.Exists(fresh) | (models.Q(vault__embedding_model__isnull=False) & models.Exists(missing)))
        )
        return pages if vault_id is None else pages.filter(vault_id=vault_id)

    def reconcile(self, *, vault_id: int | None = None) -> int:
        """Dispatch one bounded tick; oldest updates first, with deterministic ties."""

        with system_context(reason="knowledge_graph_pgvector.reconcile"):
            ids = list(
                self.stale_pages(vault_id=vault_id)
                .order_by("updated_at", "pk")
                .values_list("pk", flat=True)[:RECONCILE_BATCH]
            )
        for pk in ids:
            enqueue_task(EMBED_PAGE_TASK, kwargs={"pk": pk}, expires=RECONCILE_SECONDS, robust=True)
        return len(ids)


class TransientEmbeddingError(Exception):
    """A provider-classified transient failure, eligible for bounded task retry."""


class PassageEmbeddingManager(AngeeManager):
    """Call providers outside transactions and fence publication against live sources."""

    @staticmethod
    def validate_vectors(vectors: list[list[float]], *, count: int, dimensions: int | None) -> int:
        """Refuse malformed batches and dimension drift before touching vector rows."""

        if len(vectors) != count or not vectors:
            raise ValueError("The embedding provider returned the wrong batch size.")
        dimension = dimensions or len(vectors[0])
        if dimension < 1 or any(
            len(vector) != dimension or not all(math.isfinite(value) for value in vector) or not any(vector)
            for vector in vectors
        ):
            raise ValueError("Embedding vectors must be finite, nonzero and share the model's dimensions.")
        return dimension

    def embed_page(self, pk: int) -> int:
        """Rebuild and embed only missing passages for the vault's current model."""

        markdown_model = apps.get_model("knowledge", "MarkdownPage")
        page_model = apps.get_model("knowledge", "Page")
        vault_model = apps.get_model("knowledge", "Vault")
        passage_model = apps.get_model("knowledge_graph_pgvector", "Passage")
        with system_context(reason="knowledge_graph_pgvector.embed_page"):
            markdown = (
                markdown_model.objects.filter(
                    pk=pk,
                    vault__retrieval_class="pgvector",
                    vault__is_trashed=False,
                )
                .untrashed()
                .first()
            )
            if markdown is None:
                return 0
            require_pgvector()
            vault = markdown.vault
            model = vault.embedding_model
            if model is not None:
                model.require_approved("knowledge_embedding")
                model.require_capability({InferenceModelUse.EMBEDDING})
            passages = passage_model.objects.rebuild_for(
                markdown,
                max_input_tokens=model.max_input_tokens() if model is not None else None,
            )
            if model is None or not passages:
                return 0
            embedded = set(self.filter(passage__in=passages, model=model).values_list("passage_id", flat=True))
            pending = [passage for passage in passages if passage.pk not in embedded]
            count = 0
            dimensions = model.embedding_dimensions
            for batch in batched(pending, 32):
                try:
                    vectors = model.embed(
                        [passage.embedding_text(markdown) for passage in batch],
                        input_type="document",
                        role="knowledge_embedding",
                    )
                except Exception as error:
                    if model.is_transient_error(error):
                        raise TransientEmbeddingError(str(error)) from error
                    raise
                dimensions = self.validate_vectors(vectors, count=len(batch), dimensions=dimensions)
                with transaction.atomic():
                    # Lock the selection and both MTI source tables before publishing.
                    selected = vault_model.objects.filter(pk=vault.pk).lock_if_supported().first()
                    page_model.objects.filter(pk=pk).lock_if_supported().first()
                    current = markdown_model.objects.filter(pk=pk).lock_if_supported().first()
                    if (
                        selected is None
                        or selected.is_trashed
                        or selected.retrieval_class != "pgvector"
                        or selected.embedding_model_id != model.pk
                        or current is None
                        or current.is_trashed
                        or passage_model.objects.fingerprint(current) != passages[0].source_hash
                    ):
                        return count
                    # Serialize dimension admission across pages using this model.
                    admitted = type(model).objects.filter(pk=model.pk).lock_if_supported().get()
                    admitted.require_approved("knowledge_embedding")
                    admitted.require_capability({InferenceModelUse.EMBEDDING})
                    existing = self.filter(model_id=model.pk).order_by("pk").first()
                    self.validate_vectors(
                        vectors,
                        count=len(batch),
                        dimensions=len(existing.embedding) if existing is not None else admitted.embedding_dimensions,
                    )
                    if passage_model.objects.filter(
                        pk__in=[passage.pk for passage in batch],
                        source_hash=passages[0].source_hash,
                    ).count() != len(batch):
                        return count
                    self.bulk_create(
                        [
                            self.model(passage_id=passage.pk, model_id=model.pk, embedding=vector)
                            for passage, vector in zip(batch, vectors, strict=True)
                        ],
                        ignore_conflicts=True,
                    )
                    count += len(batch)
            return count
