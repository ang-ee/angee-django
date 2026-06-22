"""Source models for the knowledge addon.

A :class:`Vault` is the permission and namespace boundary; every
addressable thing inside it is a :class:`Page` — a thin identity row
whose kind-specific content lives in one-to-one sidecars. This addon
ships :class:`MarkdownPage`, the body sidecar for markdown-based kinds;
extension addons contribute further kinds by writing new ``kind``
values and their own sidecar model with a one-to-one to
``knowledge.Page``.
"""

from __future__ import annotations

import hashlib
import re
from typing import Any, ClassVar, cast

from django.conf import settings
from django.db import IntegrityError, models, transaction
from rebac import PermissionDenied, system_context, to_subject_ref

from angee.base.mixins import AuditMixin, HistoryMixin, RevisionMixin, SqidMixin
from angee.base.models import AngeeManager, AngeeModel

_WIKILINK_RE = re.compile(r"\[\[([^\[\]\n]+?)\]\]")


def parse_wikilinks(body: str) -> dict[str, str]:
    """Return ``{target_title: display_text}`` for each ``[[wikilink]]`` in ``body``.

    The target is the text before ``|`` with any ``#fragment`` stripped; the
    display text is the part after ``|``. First occurrence of a target wins.
    """

    found: dict[str, str] = {}
    for raw in _WIKILINK_RE.findall(body):
        target, _, display = raw.partition("|")
        target = target.split("#", 1)[0].strip()
        if target and target not in found:
            found[target] = display.strip()
    return found


class VaultManager(AngeeManager):
    """Factories for actor-owned vault writes."""

    def create_for(self, owner: Any, **fields: Any) -> Any:
        """Create a vault owned by ``owner`` after the REBAC create preflight.

        ``owner`` must be the acting user — ownership on behalf of someone
        else is refused so the row the gate authorized is the row written.
        """

        actor = self.check_create()
        if owner is None or to_subject_ref(owner) != actor:
            raise PermissionDenied(f"Denied: {actor} cannot create a vault owned by {owner!r}")
        vault = self.model(owner=owner, **fields)
        vault.full_clean()
        vault.sudo(reason="knowledge.vault.create").save()
        return vault.with_actor(actor)


class Vault(SqidMixin, AuditMixin, AngeeModel, HistoryMixin):
    """Top-level page container; the permission and namespace boundary.

    Deleting a vault cascade-deletes every page inside it; the crud delete
    mutation previews that blast radius before confirming. ``owner`` is
    protected so deleting a user account never silently wipes their vaults.
    """

    runtime = True

    sqid_prefix = "vlt_"
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        on_delete=models.PROTECT,
        related_name="owned_vaults",
    )
    name = models.CharField(max_length=200)
    description = models.TextField(blank=True, default="")
    icon = models.CharField(max_length=64, blank=True, default="")
    accent = models.CharField(max_length=32, blank=True, default="")

    objects = VaultManager()

    class Meta:
        """Django model options."""

        abstract = True
        ordering = ("name", "sqid")
        rebac_resource_type = "knowledge/vault"
        rebac_id_attr = "sqid"
        constraints = (models.UniqueConstraint(fields=("owner", "name"), name="uniq_knowledge_vault_owner_name"),)

    def __str__(self) -> str:
        """Return the vault name for Django displays."""

        return self.name


class PageManager(AngeeManager):
    """Factories for actor-scoped page writes."""

    def create_in(self, vault: Any, **fields: Any) -> Any:
        """Create a page in ``vault`` after the REBAC create preflight.

        The preflight evaluates the schema's ``create = vault->write`` with
        the relations the new row would carry, so only actors who can write
        the vault may add pages to it. A parent from another vault is
        refused — the vault is the permission boundary, and a foreign
        parent would extend ``parent->read``/``parent->write`` across it.
        """

        parent = fields.get("parent")
        if parent is not None and parent.vault_id != vault.pk:
            raise ValueError("Page parent must belong to the same vault.")
        relationships: dict[str, tuple[Any, ...]] = {"vault": (vault,)}
        if parent is not None:
            relationships["parent"] = (parent,)
        actor = self.check_create(relationships)
        page = self.model(vault=vault, **fields)
        page.full_clean()
        page.sudo(reason="knowledge.page.create").save()
        return page.with_actor(actor)


class Page(SqidMixin, AuditMixin, AngeeModel, HistoryMixin):
    """Universal addressable content node inside a vault.

    A page is thin identity — title, hierarchy, and the ``kind``
    discriminator. Kind-specific content lives in one-to-one sidecar
    models; this addon ships :class:`MarkdownPage` for markdown-based
    kinds.
    """

    runtime = True

    sqid_prefix = "pg_"

    class Kind(models.TextChoices):
        """Built-in page kinds.

        ``kind`` itself is an open ``CharField`` — extension addons store
        their own kind values and pair them with their own sidecar model
        (a one-to-one to ``knowledge.Page``) without touching this model.
        """

        NOTE = "note", "Note"
        FOLDER = "folder", "Folder"
        TEMPLATE = "template", "Template"

    vault = models.ForeignKey(
        "knowledge.Vault",
        on_delete=models.CASCADE,
        related_name="pages",
    )
    parent = models.ForeignKey(
        "self",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="children",
    )
    kind = models.CharField(max_length=16, default=Kind.NOTE, db_index=True)
    title = models.CharField(max_length=512, db_index=True)
    icon = models.CharField(max_length=64, blank=True, default="")

    objects = PageManager()

    class Meta:
        """Django model options."""

        abstract = True
        ordering = ("title", "sqid")
        rebac_resource_type = "knowledge/page"
        rebac_id_attr = "sqid"
        constraints = (models.UniqueConstraint(fields=("vault", "title"), name="uniq_knowledge_page_vault_title"),)

    def __str__(self) -> str:
        """Return the page title for Django displays."""

        return self.title


class StaleBodyError(ValueError):
    """Raised when a body write expects a hash the stored body no longer has."""


class UnsupportedPageKindError(ValueError):
    """Raised when a body write targets a page kind without a markdown sidecar."""


class MarkdownPageManager(AngeeManager):
    """Factories for actor-scoped markdown body writes."""

    def write_body(self, page: Any, body: str, *, expected_hash: str | None = None) -> Any:
        """Create or update ``page``'s markdown body, last-write-wins.

        ``expected_hash`` is an optimistic-concurrency token: when supplied
        and the stored ``body_hash`` differs, the write is rejected with
        :class:`StaleBodyError` so the caller can reload and retry.
        """

        if page.kind not in self.model.page_kinds:
            raise UnsupportedPageKindError(f"Pages of kind {page.kind!r} carry no markdown body.")
        with transaction.atomic():
            markdown = self.select_for_update().filter(page=page).first()
            if markdown is None:
                markdown = self._create_body(page, body)
                if markdown is not None:
                    return markdown
                # A concurrent first writer won the insert race; lock its row.
                markdown = self.select_for_update().get(page=page)
            if expected_hash is not None and expected_hash != markdown.body_hash:
                raise StaleBodyError("Body hash is stale; reload the page and retry.")
            markdown.body = body
            markdown.save(update_fields=("body",))
            return markdown

    def _create_body(self, page: Any, body: str) -> Any:
        """Insert the first body row, or ``None`` when a concurrent writer won."""

        actor = self.check_create({"page": (page,)})
        markdown = self.model(page=page, body=body)
        try:
            with transaction.atomic():
                markdown.sudo(reason="knowledge.markdown_page.create").save()
        except IntegrityError:
            return None
        return markdown.with_actor(actor)


class MarkdownPage(SqidMixin, AuditMixin, AngeeModel, RevisionMixin):
    """Markdown body sidecar for markdown-based page kinds.

    ``body`` is the canonical content store; ``body_hash`` and
    ``word_count`` are derived on save. Body edits are versioned through
    ``revisions`` so they can be rolled back.
    """

    runtime = True

    revisioned_fields = ("body",)

    sqid_prefix = "mdp_"

    page_kinds: ClassVar[tuple[str, ...]] = cast("tuple[str, ...]", (Page.Kind.NOTE, Page.Kind.TEMPLATE))
    """Page kinds that carry a markdown body sidecar."""

    excerpt_chars: ClassVar[int] = 180
    """Number of body characters surfaced by :attr:`excerpt`."""

    page = models.OneToOneField(
        "knowledge.Page",
        on_delete=models.CASCADE,
        related_name="markdown",
        limit_choices_to={"kind__in": page_kinds},
    )
    body = models.TextField(blank=True, default="")
    body_hash = models.CharField(max_length=64, blank=True, default="", editable=False)
    word_count = models.PositiveIntegerField(default=0, db_index=True)

    objects = MarkdownPageManager()

    class Meta:
        """Django model options."""

        abstract = True
        rebac_resource_type = "knowledge/markdown_page"
        rebac_id_attr = "sqid"

    def __str__(self) -> str:
        """Return the owning page id for Django displays."""

        return f"markdown:{self.page_id}"

    @staticmethod
    def hash_body(body: str) -> str:
        """Return the canonical content hash for one body text."""

        return hashlib.sha256(body.encode("utf-8")).hexdigest()

    @property
    def excerpt(self) -> str:
        """Return the leading body characters used for list previews."""

        if len(self.body) <= self.excerpt_chars:
            return self.body
        return self.body[: self.excerpt_chars].rstrip() + "…"

    def save(self, *args: Any, **kwargs: Any) -> None:
        """Persist the body together with its derived hash and word count."""

        self.body_hash = self.hash_body(self.body)
        self.word_count = len(self.body.split())
        update_fields = kwargs.get("update_fields")
        if update_fields is not None:
            field_names = set(update_fields)
            if "body" in field_names:
                field_names |= {"body_hash", "word_count", "updated_at"}
                kwargs["update_fields"] = field_names
        super().save(*args, **kwargs)


# ---------------------------------------------------------------------------
# Backlink index
# ---------------------------------------------------------------------------


class LinkManager(AngeeManager):
    """Owns the wikilink edge set derived from page bodies."""

    def rebuild_for(self, markdown: Any) -> None:
        """Replace the source page's outgoing links from its current body.

        The indexer is the author: it resolves ``[[title]]`` targets against
        the page's own vault and DELETE+INSERTs the edge set under
        ``system_context``. There is no per-link gate — backlink reads
        inherit the source page's permissions through the schema. A target
        created after the link still resolves on the source page's next save.
        """

        page = markdown.page
        wanted = parse_wikilinks(markdown.body)
        pages = type(page)._base_manager
        links = self.model._base_manager
        with system_context(reason="knowledge.backlinks"), transaction.atomic():
            resolved = dict(
                pages.filter(vault_id=page.vault_id)
                .exclude(pk=page.pk)
                .values_list("title", "pk")
            )
            links.filter(source_page=page).delete()
            links.bulk_create(
                [
                    self.model(
                        source_page=page,
                        target_page_id=resolved.get(target),
                        target_text=target,
                        display_text=display,
                        is_resolved=target in resolved,
                    )
                    for target, display in wanted.items()
                ]
            )


class Link(SqidMixin, AngeeModel):
    """Wikilink edge from one page to another, derived from the source body.

    Indexer-authored (see :class:`LinkManager`) — no user-facing mutation,
    no audit author. REBAC read/write inherit through ``source_page``.
    """

    runtime = True

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
        rebac_id_attr = "sqid"

    def __str__(self) -> str:
        """Return the link target text for Django displays."""

        return self.target_text
