"""Knowledge links and their native persistence owners."""

from __future__ import annotations

from collections.abc import Iterable
from typing import TYPE_CHECKING, Any, TypedDict

from django.apps import apps
from django.db import models, router, transaction
from rebac import (
    database_context,
    system_context,
)

from angee.base.models.base import AngeeDataModel
from angee.knowledge.models.pages import MarkdownPage, Page, parse_wikilinks
from angee.provenance.contrib.angee.managers import AngeeHistoryManager
from angee.provenance.contrib.angee.querysets import AngeeHistoryQuerySet

if TYPE_CHECKING:
    from django_stubs_ext import WithAnnotations


class _BacklinkTitle(TypedDict):
    source_title: str


class LinkQuerySet[LinkT: Link](AngeeHistoryQuerySet[LinkT]):
    """Readable backlink populations derived from markdown rows."""

    def backlinks_to(self, page: Page) -> LinkQuerySet[WithAnnotations[LinkT, _BacklinkTitle]]:
        """Select resolved backlinks from live readable source pages."""

        return self.filter(target_page_id=page.pk, is_resolved=True, source_page__is_trashed=False).scoped().annotate(
            source_title=models.F("source_page__title"),
        ).order_by("source_title", "sqid")


_LinkManagerBase = AngeeHistoryManager.from_queryset(LinkQuerySet)


class LinkManager[LinkT: Link](_LinkManagerBase[LinkT]):
    """Owns the wikilink edge set derived from page bodies."""

    def rebuild_for(self, markdown: Any) -> None:
        """Replace the source page's outgoing links from its current body.

        The indexer is the author: it resolves ``[[title]]`` targets against
        the page's own vault and DELETE+INSERTs the edge set under
        ``system_context``. There is no per-link gate — backlink reads
        inherit the source page's permissions through the schema. Target
        lifecycle changes also refresh the affected incoming links.
        """

        self.rebuild_many((markdown,))

    def rebuild_many(self, bodies: Iterable[MarkdownPage]) -> None:
        """Replace a batch's wikilinks, resolving titles once per source vault."""

        batch = list(bodies)
        if not batch:
            return
        page_model = apps.get_model("knowledge", "Page")
        vault_ids = {body.vault_id for body in batch}
        using = self._db or router.db_for_write(self.model, instance=batch[0])
        links = self.model._base_manager.db_manager(using)
        with database_context(using), system_context(reason="knowledge.backlinks"), transaction.atomic(using=using):
            resolved = {
                (vault_id, title): pk
                for vault_id, title, pk in page_model._base_manager.using(using).filter(
                    vault_id__in=vault_ids, is_trashed=False,
                )
                .values_list("vault_id", "title", "pk")
            }
            links.filter(source_page_id__in=[body.pk for body in batch]).delete()
            rows = []
            for body in batch:
                for target, display in parse_wikilinks(body.body).items():
                    target_id = resolved.get((body.vault_id, target))
                    if target_id == body.pk:
                        target_id = None
                    rows.append(self.model(
                        source_page_id=body.pk,
                        target_page_id=target_id,
                        target_text=target,
                        display_text=display,
                        is_resolved=target_id is not None,
                    ))
            links.bulk_create(rows)

    def refresh_targets(self, pages: Iterable[Page]) -> None:
        """Re-resolve incoming titles after target creation, rename or trash changes."""

        batch = list(pages)
        if not batch:
            return
        markdown = self.model._meta.apps.get_model("knowledge", "MarkdownPage")
        affected = models.Q(outgoing_links__target_page_id__in=[page.pk for page in batch])
        for page in batch:
            affected |= models.Q(vault_id=page.vault_id, outgoing_links__target_text=page.title)
        using = self._db or router.db_for_write(self.model, instance=batch[0])
        with database_context(using), system_context(reason="knowledge.backlinks.targets"):
            self.db_manager(using).rebuild_many(markdown._base_manager.using(using).filter(affected).distinct())


class Link(AngeeDataModel, concrete=True):
    """Wikilink edge from one page to another, derived from the source body.

    Indexer-authored (see :class:`LinkManager`) — no user-facing mutation,
    no audit author. REBAC read/write inherit through ``source_page``.
    """


    sqid_prefix = "lnk_"
    source_page = models.ForeignKey(
        "knowledge.Page",
        on_delete=models.CASCADE,
        related_name="outgoing_links",
    )
    target_page = models.ForeignKey(
        "knowledge.Page",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="incoming_links",
    )
    target_text = models.CharField(max_length=512)
    display_text = models.CharField(max_length=512, blank=True, default="")
    is_resolved = models.BooleanField(default=False, db_index=True)

    objects = LinkManager()

    class Meta:
        """Django model options."""

        abstract = True
        ordering = ("target_text", "sqid")
        rebac_resource_type = "knowledge/link"

    def __str__(self) -> str:
        """Return the link target text for Django displays."""

        return self.target_text
