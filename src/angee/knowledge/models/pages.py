"""Knowledge pages and their native persistence owners."""

from __future__ import annotations

import hashlib
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from enum import StrEnum
from graphlib import CycleError, TopologicalSorter
from typing import Any, ClassVar
from urllib.parse import quote, unquote

from django.apps import apps
from django.core.exceptions import ValidationError
from django.db import models, transaction
from markdown_it import MarkdownIt
from rebac import (
    PermissionDenied,
    SubjectRef,
    check_new,
    current_actor,
    system_context,
    to_subject_ref,
)
from rebac.errors import MissingActorError
from rebac.relation_loading import relation_actor
from rebac.resources import model_resource_type

from angee.base.fields.state import StateField
from angee.base.lineage import concrete_child, concrete_child_models
from angee.base.models.audit import AuditMixin
from angee.base.models.hierarchy import HierarchyMixin, HierarchyQuerySet
from angee.base.models.locks import OptimisticLockMixin
from angee.base.models.trash import TrashQuerySet
from angee.base.signals import send_created_batch
from angee.iam.models.base import ProtectedDataModel, ProtectedModel
from angee.iam.models.trash import ProtectedTrashMixin
from angee.iam.permissions import actor_user_id
from angee.knowledge.exceptions import (
    AmbiguousMatchError,
    SectionNotFoundError,
    StaleBodyError,
    StructuredEditError,
    UnsupportedPageKindError,
)
from angee.knowledge.models.vaults import Vault
from angee.messaging.models import ThreadedModelMixin
from angee.provenance.context import write_group
from angee.provenance.contrib.angee.history import HistoryMixin
from angee.provenance.contrib.angee.managers import AngeeHistoryManager
from angee.provenance.contrib.angee.querysets import AngeeHistoryQuerySet
from angee.tags.models import TaggedModel

_WIKILINK_RE = re.compile(r"\[\[([^\[\]\n]+?)\]\]")


_WIKILINK_PREFIX = "wikilink:"


_MD = MarkdownIt("commonmark")


_SLUG_DROP_RE = re.compile(r"[^\w\s-]")


_SLUG_DASH_RE = re.compile(r"[\s_]+")


_SLUG_SQUEEZE_RE = re.compile(r"-+")


@dataclass(frozen=True)
class OutlineEntry:
    """One ATX heading in a markdown body's outline.

    ``line`` is the 0-based source line of the heading in the CRLF-normalized
    body, the coordinate :meth:`MarkdownPage.section_range` slices on.
    """

    level: int
    text: str
    slug: str
    line: int


def parse_wikilinks(body: str) -> dict[str, str]:
    """Index native wikilinks and Markdown links to ``wikilink:encoded-title``.

    Markdown owns code and link syntax. Wiki brackets strip a heading fragment;
    encoded link destinations preserve literal title punctuation. First wins.
    """

    found: dict[str, str] = {}
    for block in _MD.parse(body):
        children = block.children or []
        for index, token in enumerate(children):
            if token.type == "text":
                for raw in _WIKILINK_RE.findall(token.content):
                    target, _, display = raw.partition("|")
                    target = target.split("#", 1)[0].strip()
                    if target:
                        found.setdefault(target, display.strip())
            elif token.type == "link_open" and (href := str(token.attrGet("href") or "")).startswith(_WIKILINK_PREFIX):
                target = unquote(href[len(_WIKILINK_PREFIX):])
                label = []
                for child in children[index + 1:]:
                    if child.type == "link_close":
                        break
                    label.append(child.content)
                if target:
                    found.setdefault(target, "".join(label))
    return found


class PageQuerySet[PageT: Page](HierarchyQuerySet[PageT], TrashQuerySet[PageT], AngeeHistoryQuerySet[PageT]):
    """Actor-scoped page reads; ``untrashed()`` is every page's default surface."""

    def navigation_root_expression(self, *, removed: bool = False) -> models.Expression:
        """A visible tree root has no readable parent in the same trash state."""

        actor = relation_actor(self)
        parents = self.none() if actor is None else self.with_actor(actor).filter(is_trashed=removed).scoped()
        return ~models.Exists(parents.filter(pk=models.OuterRef("parent_id")))

    def has_children_expression(self) -> models.Exists:
        """Whether the actor can expand this page to an untrashed child."""

        actor = relation_actor(self)
        children = self.none() if actor is None else self.with_actor(actor).untrashed().scoped()
        return models.Exists(children.filter(parent_id=models.OuterRef("pk")))


_PageManagerBase = AngeeHistoryManager.from_queryset(PageQuerySet)


class PageManager[PageT: Page](_PageManagerBase[PageT]):
    """Factories for actor-scoped page writes."""

    def create_in(self, vault: Any, **fields: Any) -> PageT:
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
        actor = current_actor()
        if actor is None:
            raise MissingActorError("Creating a knowledge page requires an actor.")
        relationships = {"vault": (to_subject_ref(vault),)}
        if parent is not None:
            relationships["parent"] = (to_subject_ref(parent),)
        resource_type = model_resource_type(self.model)
        if resource_type is None or not check_new(
            subject=to_subject_ref(actor),
            action="create",
            resource_type=resource_type,
            relationships=relationships,
        ).allowed:
            raise PermissionDenied(f"Denied: {actor} cannot create {resource_type or 'knowledge/page'}")
        kind = fields.pop("kind", self.model.PageKind.NOTE)
        page_model: type[Page] = self.model
        if kind != self.model.PageKind.FOLDER:
            if kind not in (self.model.PageKind.NOTE, self.model.PageKind.TEMPLATE):
                raise ValueError(f"Unsupported page kind: {kind!r}")
            page_model = self.model._meta.apps.get_model("knowledge", "MarkdownPage")
            fields["kind"] = kind
        # The create preflight above authorizes this instance. Carry that verdict
        # through native constraint validation as well as persistence.
        page = page_model(vault=vault, **fields).sudo(reason="knowledge.page.create")
        page.full_clean()
        page.save()
        return self.model._base_manager.get(pk=page.pk).with_actor(actor)

    def _copy_tree_in(
        self, vault: Vault, pages: Mapping[Any, Page], *, actor: SubjectRef,
    ) -> dict[Any, PageT]:
        """Copy template pages within the authorized vault-cloning transaction.

        The clone owner supplies actor-readable source pages. Their unchanged
        scalar values retain source validation. The hierarchy owner inserts the
        tree level by level and derives each path (``create_forest``), recording
        each page's creation once; lifecycle subscribers then hear of each page.
        A cloned tree is a copy, not authored pages: nobody is made to follow them.
        """

        user_id = actor_user_id(actor)
        for page in pages.values():
            if page.parent_id is not None and page.parent_id not in pages:
                raise ValidationError("A template page's parent is unavailable or unreadable in this vault.")
        tree = TopologicalSorter({
            pk: (page.parent_id,) if page.parent_id is not None else ()
            for pk, page in pages.items()
        })
        try:
            order = tuple(tree.static_order())
        except CycleError as error:
            raise ValidationError("Template page parents must form a tree.") from error
        copies: dict[Any, PageT] = {}
        for pk in order:
            source = pages[pk]
            copies[pk] = self.model(
                vault=vault,
                parent=copies.get(source.parent_id),
                title=source.title,
                icon=source.icon,
                created_by_id=user_id,
                updated_by_id=user_id,
            )
        elevated = self.sudo(reason="knowledge.page.clone")
        with write_group():
            elevated.create_forest(list(copies.values()))
            apps.get_model("knowledge", "Link").objects.refresh_targets(copies.values())
            for page in copies.values():
                page.with_actor(actor)
            send_created_batch(
                sender=self.model, instances=copies.values(), using=self.db, knowledge_backlinks_rebuilt=True,
            )
        return copies


class Page(
    OptimisticLockMixin, TaggedModel, ThreadedModelMixin, HierarchyMixin, ProtectedTrashMixin, AuditMixin, HistoryMixin,
    ProtectedDataModel, concrete=True
):
    """Universal addressable content node inside a vault.

    A page owns title and hierarchy. A concrete child owns each content shape;
    a parent row without a child is a folder. Trashing a page trashes every
    page below it with the same stamp, so each stored flag is exact; restoring
    it brings back the pages trashed with it. Trashed pages are withheld from
    readers who cannot delete them and leave their titles free meanwhile.
    """

    rebac_grantable = {"viewer": "write"}

    sqid_prefix = "pg_"
    hierarchy_scope_fields = ("vault",)

    class PageKind(models.TextChoices):
        """Built-in page kinds.

        Note and template share the markdown shape and differ only in intent.
        """

        NOTE = "note", "Note"
        FOLDER = "folder", "Folder"
        TEMPLATE = "template", "Template"

    vault = models.ForeignKey(
        "knowledge.Vault",
        on_delete=models.CASCADE,
        related_name="pages",
    )
    # A page's subtree is its content: deleting a folder page deletes the pages
    # below it, where the hierarchy's default protects a parent with children.
    parent = models.ForeignKey("self", on_delete=models.CASCADE, null=True, blank=True, related_name="children")
    title = models.CharField(max_length=512, db_index=True)
    icon = models.CharField(max_length=64, blank=True, default="")

    objects = PageManager()

    class Meta(HierarchyMixin.Meta):
        """Django model options."""

        abstract = True
        ordering = ("title", "sqid")
        rebac_resource_type = "knowledge/page"
        constraints = (
            models.UniqueConstraint(
                fields=("vault", "title"),
                condition=models.Q(is_trashed=False),
                name="uniq_knowledge_page_vault_title",
                violation_error_message="A page with this title already exists in this vault.",
            ),
        )

    def __str__(self) -> str:
        """Return the page title for Django displays."""

        return self.title

    @property
    def wikilink_url(self) -> str:
        """Portable Markdown destination for this page within its own vault."""

        return f"{_WIKILINK_PREFIX}{quote(self.title, safe='')}"

    @property
    def kind(self) -> str:
        """Project the page's kind from its concrete child."""

        for child_model in concrete_child_models(self._meta.apps.get_model("knowledge", "Page")):
            child = concrete_child(self, child_model)
            if child is not None:
                if child_model is self._meta.apps.get_model("knowledge", "MarkdownPage"):
                    return str(child.kind)
                model_name = child._meta.model_name
                assert model_name is not None
                return model_name
        return str(self.PageKind.FOLDER)

    def clean(self) -> None:
        """Keep live pages out of trashed folders."""

        super().clean()
        if self.parent_id is not None and not self.is_trashed and type(self)._base_manager.filter(
            pk=self.parent_id, is_trashed=True,
        ).exists():
            raise ValidationError({"parent": "Page parent is in the trash."})

    def trash(self, *, reason: str = "") -> None:
        """Trash this page and, with the same stamp, every untrashed page below it."""

        db = self._state.db
        with transaction.atomic(using=db):
            super().trash(reason=reason)
            pages = type(self).system_queryset().using(db)
            pages.subtree_of(self).exclude(pk=self.pk).trash(
                reason=self.trash_reason, at=self.trashed_at,
            )

    def restore(self) -> None:
        """Restore this page and the pages below it that were trashed with it.

        Refuses while the containing folder is trashed, and when an untrashed
        page took a restored title meanwhile.
        """

        db = self._state.db
        with transaction.atomic(using=db):
            pages = type(self).system_queryset(lock=("self",)).using(db)
            if self.parent_id is not None and pages.filter(pk=self.parent_id, is_trashed=True).exists():
                raise ValidationError({"parent": "Restore the folder that contains this page first."})
            together = pages.subtree_of(self).exclude(pk=self.pk).filter(trashed_at=self.trashed_at).trashed()
            titles = [self.title, *together.values_list("title", flat=True)]
            if pages.filter(vault_id=self.vault_id, title__in=titles).untrashed().exists():
                raise ValidationError({"title": "Another page in this vault already uses a restored title."})
            super().restore()
            together.restore()












class MarkdownPageManager[MarkdownT: MarkdownPage](PageManager[MarkdownT]):
    """Factories for actor-scoped markdown body writes."""

    def _copy_bodies(
        self,
        bodies: Iterable[MarkdownPage],
        pages: Mapping[Any, Page],
        *,
        actor: SubjectRef,
    ) -> None:
        """Insert child bodies for pages admitted by the vault clone preflight.

        One INSERT per body: Django's ``bulk_create`` refuses multi-table children.
        """

        copies = []
        with write_group():
            with system_context(reason="knowledge.vault.clone.markdown"):
                for source in bodies:
                    page = pages[source.pk]
                    body = self.model(
                        **{field.attname: getattr(page, field.attname) for field in page._meta.concrete_fields},
                        page_ptr=page,
                        kind=source.kind,
                        body=source.body,
                        body_hash=source.body_hash,
                        word_count=source.word_count,
                    )
                    body.save_base(raw=True, force_insert=True, using=self.db)
                    copies.append(body.with_actor(actor))
            apps.get_model("knowledge", "Link").objects.rebuild_many(copies)
            send_created_batch(
                sender=self.model, instances=copies, using=self.db, knowledge_backlinks_rebuilt=True,
            )

    def write_body(
        self, page: Any, body: str, *, expected_hash: str | None = None, title: str | None = None,
        expected_revision: int | None = None,
    ) -> MarkdownT:
        """Update the markdown body and optional page title in one transaction.

        ``expected_hash`` is an optimistic-concurrency token: when supplied
        and the stored ``body_hash`` differs, the write is rejected with
        :class:`StaleBodyError` so the caller can reload and retry. The native
        Page revision also covers metadata edits; callers saving a title pass
        ``expected_revision`` from the same loaded page.
        """

        if page.kind == Page.PageKind.FOLDER:
            raise UnsupportedPageKindError(f"Pages of kind {page.kind!r} carry no markdown body.")
        actor = current_actor()
        rows = self.with_actor(actor) if actor is not None else self.all()
        locked = rows.select_for_update()
        using = locked.db
        with transaction.atomic(using=using):
            markdown = locked.get(pk=page.pk)
            if expected_hash is not None and expected_hash != markdown.body_hash:
                raise StaleBodyError("Body hash is stale; reload the page and retry.")
            markdown.body = body
            fields = {"body"}
            if title is not None:
                markdown.title = title
                markdown.full_clean()
                fields.add("title")
            markdown.save(using=using, update_fields=fields, expected_revision=expected_revision)
            return markdown

    # -- structure-aware edits --------------------------------------------
    # Thin write-orchestrators: read the current body (actor-scoped), splice it
    # through the body's own structure staticmethods (the single markdown owner),
    # then persist through :meth:`write_body`. ``expected_hash`` is threaded
    # **unchanged** so the locked CAS in ``write_body`` stays authoritative — the
    # local read here only computes candidate text, never the hash that is checked.
    # Each inherits CAS, revision recording, and backlink rebuild from ``write_body``.

    def patch_section(
        self,
        page: Any,
        heading_path: str | list[str],
        op: str,
        content: str,
        *,
        expected_hash: str | None = None,
    ) -> MarkdownT:
        """Replace/append/prepend the section at ``heading_path`` and write the body.

        Splices through :meth:`MarkdownPage.spliced_section`, which fails fast with
        :class:`SectionNotFoundError`/:class:`AmbiguousMatchError` before any write.
        """

        new_body = self.model.spliced_section(self._current_body(page), heading_path, op, content)
        return self.write_body(page, new_body, expected_hash=expected_hash)

    def replace_unique(
        self,
        page: Any,
        old: str,
        new: str,
        *,
        expected_hash: str | None = None,
    ) -> MarkdownT:
        """Replace the single occurrence of ``old`` with ``new`` and write the body.

        Splices through :meth:`MarkdownPage.spliced_unique` (exact-string, uniqueness
        enforced), so a non-unique or absent target fails fast before any write.
        """

        new_body = self.model.spliced_unique(self._current_body(page), old, new)
        return self.write_body(page, new_body, expected_hash=expected_hash)

    def append(self, page: Any, content: str, *, expected_hash: str | None = None) -> MarkdownT:
        """Append ``content`` to the end of ``page``'s body and write it."""

        new_body = self.model.appended(self._current_body(page), content)
        return self.write_body(page, new_body, expected_hash=expected_hash)

    def prepend(self, page: Any, content: str, *, expected_hash: str | None = None) -> MarkdownT:
        """Prepend ``content`` to the start of ``page``'s body and write it."""

        new_body = self.model.prepended(self._current_body(page), content)
        return self.write_body(page, new_body, expected_hash=expected_hash)

    def _current_body(self, page: Any) -> str:
        """Return ``page``'s current body as the actor can read it, or ``""``.

        This read is unlocked, so the splice is computed against a body the locking
        ``write_body`` does not pin: with ``expected_hash`` the CAS still rejects any
        concurrent change (the stored hash differs); with ``expected_hash=None`` the
        edit is last-write-wins by design. The CAS in ``write_body`` stays the single
        authority — this read only computes candidate text, never the checked hash.
        """

        markdown = self.filter(pk=page.pk).first()
        return "" if markdown is None else markdown.body


class SectionOp(StrEnum):
    """How a markdown section is spliced; member names are GraphQL wire values."""

    REPLACE = "replace"
    APPEND = "append"
    PREPEND = "prepend"


class MarkdownPage(HistoryMixin, ProtectedModel, inherits="knowledge.Page"):
    """Concrete page with a versioned markdown body.

    ``body`` is the canonical content store; ``body_hash`` and
    ``word_count`` are derived on save. The child declares its own ``history``
    so snapshots include the body alongside inherited page metadata.
    """


    excerpt_chars: ClassVar[int] = 180
    """Number of body characters surfaced by :attr:`excerpt`."""

    kind = StateField(choices_enum=Page.PageKind, default=Page.PageKind.NOTE)
    body = models.TextField(blank=True, default="")
    body_hash = models.CharField(max_length=64, blank=True, default="", editable=False)
    word_count = models.PositiveIntegerField(default=0, db_index=True)

    objects = MarkdownPageManager()

    class Meta:
        """Django model options."""

        abstract = True
        # Django creates the concrete MTI page_ptr; preserve Page.markdown.
        default_related_name = "markdown"
        rebac_resource_type = "knowledge/markdown_page"
        constraints = (
            models.CheckConstraint(condition=~models.Q(kind=Page.PageKind.FOLDER), name="ck_markdown_page_not_folder"),
        )

    def __str__(self) -> str:
        """Return the inherited page title for Django displays."""

        return self.title

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

    # -- markdown structure ------------------------------------------------
    # The body lives here, so its structure behaviour lives here too: the
    # heading outline, a section's line range, and section/exact-string
    # splices that never re-render (non-heading markdown round-trips
    # byte-for-byte). markdown-it-py supplies the block tokens' source line
    # spans; everything else is raw line-buffer slicing.

    @property
    def outline(self) -> list[OutlineEntry]:
        """Return this body's heading outline (see :meth:`parse_outline`)."""

        return self.parse_outline(self.body)

    @staticmethod
    def parse_outline(body: str) -> list[OutlineEntry]:
        """Return the ordered ATX headings in ``body`` as :class:`OutlineEntry`.

        Heading levels and source lines come straight from markdown-it-py's
        ``heading_open`` block tokens (``.tag`` → level, ``.map[0]`` → line);
        the text is the following inline token's ``.content``. Setext (underline)
        headings are skipped — section addressing keys on single-line ATX
        headings.
        """

        tokens = _MD.parse(MarkdownPage._normalize_newlines(body))
        return [
            OutlineEntry(
                level=int(token.tag[1:]),
                text=tokens[index + 1].content,
                slug=MarkdownPage._slug(tokens[index + 1].content),
                line=token_map[0],
            )
            for index, token in enumerate(tokens)
            if token.type == "heading_open" and token.markup.startswith("#") and (token_map := token.map) is not None
        ]

    @staticmethod
    def section_range(body: str, heading_path: str | list[str]) -> tuple[int, int]:
        """Resolve ``heading_path`` to its ``[start, end)`` line range in ``body``.

        ``heading_path`` is a single heading text or an ancestor chain
        (``["Usage", "CLI"]``); it tail-matches each heading's ancestor path,
        case-insensitively, so ``["CLI"]`` and the qualified path both resolve.
        Lines are 0-based into the CRLF-normalized body. The range runs from the
        heading line to the next heading of the same-or-higher level (children
        included), or end-of-body. Fail-fast: :class:`SectionNotFoundError` when
        nothing matches, :class:`AmbiguousMatchError` when more than one does.
        """

        normalized = MarkdownPage._normalize_newlines(body)
        entries = MarkdownPage.parse_outline(normalized)
        line_count = len(normalized.split("\n"))
        want = [text.strip().lower() for text in ([heading_path] if isinstance(heading_path, str) else heading_path)]
        matches: list[tuple[int, int]] = []
        ancestry: list[OutlineEntry] = []
        for index, entry in enumerate(entries):
            while ancestry and ancestry[-1].level >= entry.level:
                ancestry.pop()
            ancestry.append(entry)
            tail = [ancestor.text.strip().lower() for ancestor in ancestry][-len(want) :]
            if tail != want:
                continue
            end = next(
                (later.line for later in entries[index + 1 :] if later.level <= entry.level),
                line_count,
            )
            matches.append((entry.line, end))
        if not matches:
            raise SectionNotFoundError(f"No section matches heading path {heading_path!r}.")
        if len(matches) > 1:
            raise AmbiguousMatchError(f"Heading path {heading_path!r} is ambiguous ({len(matches)} matches).")
        return matches[0]

    @staticmethod
    def spliced_section(body: str, heading_path: str | list[str], op: SectionOp | str, content: str) -> str:
        """Return ``body`` with one section's content spliced, never re-rendered.

        ``op`` is a :class:`SectionOp`: ``replace`` swaps the section body,
        ``append``/``prepend`` add ``content`` after/before it (after nested
        children for ``append`` — the range is section-inclusive). The heading
        line and everything outside the section are byte-identical (after CRLF
        normalization). One blank line separates the section from the next
        heading (or terminates the body); blank lines inside the preserved body
        — e.g. inside a code block — are untouched.
        """

        try:
            op = SectionOp(op)
        except ValueError as error:
            raise StructuredEditError(f"Unknown section op {op!r}; expected one of {tuple(SectionOp)}.") from error
        normalized = MarkdownPage._normalize_newlines(body)
        start, end = MarkdownPage.section_range(normalized, heading_path)
        lines = normalized.split("\n")
        existing = lines[start + 1 : end]
        addition = MarkdownPage._normalize_newlines(content).split("\n")
        blocks = {
            SectionOp.REPLACE: [addition],
            SectionOp.PREPEND: [addition, existing],
            SectionOp.APPEND: [existing, addition],
        }[op]
        section_body = MarkdownPage._join_blocks(blocks)
        spliced = [lines[start]]
        if section_body:
            spliced.append("")
            spliced.extend(section_body)
        if lines[end:] or normalized.endswith("\n"):
            spliced.append("")
        return "\n".join([*lines[:start], *spliced, *lines[end:]])

    @staticmethod
    def spliced_unique(body: str, old: str, new: str) -> str:
        """Return ``body`` with the single occurrence of ``old`` replaced by ``new``.

        Exact-string match, uniqueness enforced: :class:`SectionNotFoundError`
        when ``old`` is absent, :class:`AmbiguousMatchError` when it occurs more
        than once, so an edit can never silently land on the wrong span.
        """

        count = body.count(old)
        if count == 0:
            raise SectionNotFoundError(f"Text to replace not found: {old!r}.")
        if count > 1:
            raise AmbiguousMatchError(f"Text to replace is not unique ({count} occurrences): {old!r}.")
        return body.replace(old, new, 1)

    @staticmethod
    def appended(body: str, content: str) -> str:
        """Return ``body`` with ``content`` joined after it, one blank line apart.

        Whole-body assembly counterpart to :meth:`spliced_section`: ``content`` lands
        after the existing text with the same single-blank seam :meth:`_join_blocks`
        gives a section splice — no markdown is parsed or re-rendered.
        """

        return MarkdownPage._joined(body, content, prepend=False)

    @staticmethod
    def prepended(body: str, content: str) -> str:
        """Return ``body`` with ``content`` joined before it, one blank line apart.

        The prepend counterpart to :meth:`appended` (same single-blank seam).
        """

        return MarkdownPage._joined(body, content, prepend=True)

    @staticmethod
    def _joined(body: str, content: str, *, prepend: bool) -> str:
        """Join ``body`` and ``content`` at one end, one blank line apart (no parse)."""

        base = MarkdownPage._normalize_newlines(body).split("\n")
        added = MarkdownPage._normalize_newlines(content).split("\n")
        blocks = [added, base] if prepend else [base, added]
        return "\n".join(MarkdownPage._join_blocks(blocks))

    @staticmethod
    def _normalize_newlines(text: str) -> str:
        """Return ``text`` with CRLF/CR line endings collapsed to ``\\n``."""

        return text.replace("\r\n", "\n").replace("\r", "\n")

    @staticmethod
    def _slug(text: str) -> str:
        """Return a GitHub-ish anchor slug for one heading's text."""

        lowered = _SLUG_DROP_RE.sub("", text.strip().lower())
        return _SLUG_SQUEEZE_RE.sub("-", _SLUG_DASH_RE.sub("-", lowered)).strip("-")

    @staticmethod
    def _join_blocks(blocks: list[list[str]]) -> list[str]:
        """Join line-blocks with exactly one blank line between non-empty blocks.

        Each block's own leading/trailing blank lines are trimmed so the seam
        carries a single separator; blank lines *inside* a block (e.g. inside a
        fenced or indented code block) are preserved verbatim.
        """

        trimmed: list[list[str]] = []
        for block in blocks:
            lines = list(block)
            while lines and lines[0] == "":
                lines.pop(0)
            while lines and lines[-1] == "":
                lines.pop()
            if lines:
                trimmed.append(lines)
        joined: list[str] = []
        for index, block in enumerate(trimmed):
            if index:
                joined.append("")
            joined.extend(block)
        return joined

    def refresh_body_metadata(self) -> None:
        """Derive hash and word count for both single and batch body writes."""

        self.body_hash = self.hash_body(self.body)
        self.word_count = len(self.body.split())

    def save(self, *args: Any, **kwargs: Any) -> None:
        """Persist the body together with its derived hash and word count."""

        self.refresh_body_metadata()
        update_fields = kwargs.get("update_fields")
        if update_fields is not None:
            field_names = set(update_fields)
            if "body" in field_names:
                field_names |= {"body_hash", "word_count", "updated_at"}
                kwargs["update_fields"] = field_names
        super().save(*args, **kwargs)
