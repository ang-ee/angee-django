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
from dataclasses import dataclass
from typing import Any, ClassVar, cast

from django.conf import settings
from django.db import IntegrityError, models, transaction
from markdown_it import MarkdownIt
from rebac import PermissionDenied, system_context, to_subject_ref

from angee.base.fields import ImplClassField
from angee.base.mixins import AuditMixin, HistoryMixin, RevisionMixin, SqidMixin
from angee.base.models import AngeeManager, AngeeModel
from angee.knowledge.retrieval import RetrievalBackend

_WIKILINK_RE = re.compile(r"\[\[([^\[\]\n]+?)\]\]")

# CommonMark tokenizer reused for every outline parse; we only consume block
# tokens' source line spans (``.map``) and the heading inline ``.content``, so a
# single shared instance is safe and cheap (see ``MarkdownPage.parse_outline``).
_MD = MarkdownIt("commonmark")

# Slug shaping for heading anchors: drop punctuation, collapse whitespace/
# underscores to single hyphens (GitHub-ish). Anchors are advisory — section
# addressing keys on the heading path, not the slug.
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
    retrieval_class = ImplClassField(
        base_class=RetrievalBackend,
        registry_setting="ANGEE_KNOWLEDGE_RETRIEVAL_CLASSES",
        default="lexical",
    )
    """Registry key for the retrieval backend this vault searches through."""

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

    @property
    def retrieval(self) -> RetrievalBackend:
        """Return the retrieval backend this vault's ``retrieval_class`` selects.

        The vault is both the search namespace and the per-namespace selection
        point: ``retrieval_class`` names the backend (default ``lexical``) and this
        binds it, mirroring ``InferenceProvider.backend``.
        """

        return self.retrieval_for(self.retrieval_class)

    def retrieval_for(self, key: str) -> RetrievalBackend:
        """Return the registered retrieval backend for ``key``, bound to this vault.

        The single public resolution seam over the vault-owned ``retrieval_class``
        registry: callers (this model's ``retrieval`` property, a semantic plugin
        forcing its own ``key``) ask the vault rather than re-deriving the field's
        internals — so ``ImplClassField`` stays the only thing that decodes the
        registry, and the boundary is a method, not ``_meta`` shape-probing.
        """

        backend_class = cast("type[RetrievalBackend]", type(self).resolve_impl_class("retrieval_class", key))
        return backend_class(self)


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


class StructuredEditError(ValueError):
    """Base for a structure-aware markdown edit that cannot be applied."""


class SectionNotFoundError(StructuredEditError):
    """Raised when a heading path (or replace target) matches nothing."""


class AmbiguousMatchError(StructuredEditError):
    """Raised when a heading path (or replace target) matches more than once."""


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
    ) -> Any:
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
    ) -> Any:
        """Replace the single occurrence of ``old`` with ``new`` and write the body.

        Splices through :meth:`MarkdownPage.spliced_unique` (exact-string, uniqueness
        enforced), so a non-unique or absent target fails fast before any write.
        """

        new_body = self.model.spliced_unique(self._current_body(page), old, new)
        return self.write_body(page, new_body, expected_hash=expected_hash)

    def append(self, page: Any, content: str, *, expected_hash: str | None = None) -> Any:
        """Append ``content`` to the end of ``page``'s body and write it."""

        new_body = self.model.appended(self._current_body(page), content)
        return self.write_body(page, new_body, expected_hash=expected_hash)

    def prepend(self, page: Any, content: str, *, expected_hash: str | None = None) -> Any:
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

        markdown = self.filter(page=page).first()
        return "" if markdown is None else markdown.body


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

    SECTION_OPS: ClassVar[tuple[str, ...]] = ("replace", "append", "prepend")
    """Section splice operations accepted by :meth:`spliced_section`."""

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
            if token.type == "heading_open"
            and token.markup.startswith("#")
            and (token_map := token.map) is not None
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
    def spliced_section(body: str, heading_path: str | list[str], op: str, content: str) -> str:
        """Return ``body`` with one section's content spliced, never re-rendered.

        ``op`` is one of :attr:`SECTION_OPS`: ``replace`` swaps the section body,
        ``append``/``prepend`` add ``content`` after/before it (after nested
        children for ``append`` — the range is section-inclusive). The heading
        line and everything outside the section are byte-identical (after CRLF
        normalization). One blank line separates the section from the next
        heading (or terminates the body); blank lines inside the preserved body
        — e.g. inside a code block — are untouched.
        """

        if op not in MarkdownPage.SECTION_OPS:
            raise StructuredEditError(f"Unknown section op {op!r}; expected one of {MarkdownPage.SECTION_OPS}.")
        normalized = MarkdownPage._normalize_newlines(body)
        start, end = MarkdownPage.section_range(normalized, heading_path)
        lines = normalized.split("\n")
        existing = lines[start + 1 : end]
        addition = MarkdownPage._normalize_newlines(content).split("\n")
        blocks = {"replace": [addition], "prepend": [addition, existing], "append": [existing, addition]}[op]
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
            resolved = dict(pages.filter(vault_id=page.vault_id).exclude(pk=page.pk).values_list("title", "pk"))
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
