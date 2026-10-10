"""Source models for the knowledge addon.

A :class:`Vault` is the permission and namespace boundary; every
addressable thing inside it is a :class:`Page`. A :class:`MarkdownPage`
is the concrete child for pages with a versioned markdown body; extension
addons contribute their own child models for other content shapes.
"""

from __future__ import annotations

import hashlib
import logging
import re
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass
from enum import StrEnum
from graphlib import CycleError, TopologicalSorter
from itertools import islice
from typing import Any, ClassVar, cast

import reversion
from django.apps import apps
from django.contrib.contenttypes.fields import GenericForeignKey
from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import ValidationError
from django.db import models, router, transaction
from django.db.models.signals import post_save
from markdown_it import MarkdownIt
from rebac import (
    PermissionDenied,
    SubjectRef,
    actor_context,
    generic_target,
    system_context,
    to_subject_ref,
)
from rebac.mixins import RebacModelBase

from angee.base.actors import actor_user_id
from angee.base.fields import StateField
from angee.base.impl import ImplClassField
from angee.base.mixins import (
    AuditMixin,
    CreationKeyMixin,
    CreationKeyQuerySet,
    HistoryMixin,
    OwnerMixin,
    RevisionMixin,
    TrashMixin,
    TrashQuerySet,
)
from angee.base.models import AngeeDataModel, AngeeManager, AngeeQuerySet
from angee.base.refs import (
    MergePolicy,
    RecordRef,
    RecordRefMixin,
    concrete_child,
    concrete_child_accessor,
    concrete_child_models,
)
from angee.base.scoping import lock_if_supported
from angee.knowledge.retrieval import RetrievalBackend
from angee.tags.models import TaggedModel

_WIKILINK_RE = re.compile(r"\[\[([^\[\]\n]+?)\]\]")
logger = logging.getLogger(__name__)

MAX_SEARCH_PAGE_SIZE = 100
MAX_SEARCH_VAULTS = 100

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


class VaultQuerySet(CreationKeyQuerySet[Any], AngeeQuerySet[Any]):
    """Actor-scoped vault reads with caller-scoped creation replay."""

    def search_pages(self, query: str, *, first: int = 20) -> list[Any]:
        """Search at most 100 readable vaults, once per selected backend class.

        Vault name/id order bounds the work set. Backend-key order is stable;
        each backend owns ranking within its group and the page budget is total.
        """
        limit = max(0, min(first, MAX_SEARCH_PAGE_SIZE))
        if not limit:
            return []
        groups: dict[str, list[Any]] = {}
        for vault in self.scoped().order_by("name", "sqid")[:MAX_SEARCH_VAULTS]:
            groups.setdefault(vault.retrieval_class, []).append(vault)
        field = self.model._meta.get_field("retrieval_class")
        pages: list[Any] = []
        for key, vaults in sorted(groups.items()):
            remaining = limit - len(pages)
            implementation = field.resolve_class(key)
            pages.extend(islice(implementation.search_many(vaults, query, first=remaining), remaining))
            if len(pages) == limit:
                break
        return pages


class VaultManager(AngeeManager.from_queryset(VaultQuerySet)):  # type: ignore[misc]
    """Factories for actor-owned vault writes."""

    def create_for(self, owner: Any, **fields: Any) -> Any:
        """Create a vault owned by ``owner`` after the REBAC create preflight.

        ``owner`` must be the acting user — ownership on behalf of someone
        else is refused so the row the gate authorized is the row written.
        """

        actor = self.check_create()
        if owner is None or to_subject_ref(owner) != actor:
            raise PermissionDenied(f"Denied: {actor} cannot create a vault owned by {owner!r}")
        return self._create_for_actor(actor, **fields)

    def create_from(
        self,
        template: Vault,
        *,
        name: str,
        owned: bool = True,
        client_creation_key: str | None = None,
    ) -> Vault:
        """Clone a readable vault's page tree and markdown bodies for the actor.

        Requires vault create and, for a new clone, template read. New identities and attribution
        belong to the actor; grants and record bindings are never copied. Unknown
        page kinds are refused rather than copied without their content.
        ``owned=False`` inserts the clone without an owner. A replay
        key belongs to the creating actor, even after ownership is transferred or
        cleared; changing template, name or ownership intent conflicts. Template
        edits, loss of access or deletion do not change an already completed clone.
        """

        actor = self.check_create()
        user_id = actor_user_id(actor)
        if user_id is None:
            raise PermissionDenied("Cloning a vault requires a user actor.")
        fingerprint = self.model.creation_fingerprint_for([template.pk, name, owned])
        # A successful ownerless clone is not necessarily readable by its caller.
        # Resolve only this actor's creation receipt, then rebind the returned row.
        replays = self.system_context(reason="knowledge.vault.clone.replay")

        def insert() -> Vault:
            source = self.with_actor(actor).get(pk=template.pk)
            page_model = apps.get_model("knowledge", "Page")
            markdown_model = apps.get_model("knowledge", "MarkdownPage")
            pages = {
                page.pk: page
                for page in (
                    page_model._default_manager.with_actor(actor).filter(vault=source).untrashed().order_by(
                        "pk"
                    ).prefetch_related(
                        *(
                            models.Prefetch(
                                concrete_child_accessor(page_model, child_model),
                                queryset=child_model._base_manager.only(
                                    "pk", *("kind",) if child_model is markdown_model else (),
                                ),
                            )
                            for child_model in concrete_child_models(page_model)
                        )
                    )
                )
            }
            for page in pages.values():
                if page.kind not in (Page.PageKind.NOTE, Page.PageKind.TEMPLATE, Page.PageKind.FOLDER):
                    raise UnsupportedPageKindError(f"Cannot clone pages of kind {page.kind!r}.")
            bodies = list(markdown_model._default_manager.with_actor(actor).filter(pk__in=pages))
            if {body.pk for body in bodies} != {
                page.pk for page in pages.values() if page.kind != Page.PageKind.FOLDER
            }:
                raise UnsupportedPageKindError("Cannot clone a page whose content is unavailable.")
            vault = self._create_for_actor(
                actor,
                owned=owned,
                name=name,
                description=source.description,
                icon=source.icon,
                accent=source.accent,
                retrieval_class=source.retrieval_class,
                client_creation_key=client_creation_key,
                creation_fingerprint=fingerprint,
            )
            copies = page_model._default_manager._copy_tree_in(vault, pages, actor=actor)
            markdown_model._default_manager._copy_bodies(bodies, copies, actor=actor)
            return cast(Vault, vault)

        vault, _created = replays.replay_or_insert(user_id, client_creation_key, fingerprint, insert)
        return cast(Vault, vault.with_actor(actor))

    def _create_for_actor(self, actor: SubjectRef, *, owned: bool = True, **fields: Any) -> Any:
        """Persist a vault after its caller's create preflight."""

        owner_id = actor_user_id(actor)
        if owner_id is None:
            raise PermissionDenied("An actor-owned vault requires a user actor.")
        vault = self.model(
            owner_id=owner_id if owned else None,
            created_by_id=owner_id,
            updated_by_id=owner_id,
            **fields,
        )
        vault.full_clean()
        if owned:
            vault.sudo(reason="knowledge.vault.create").save()
        else:
            # Native bulk insertion bypasses OwnerMixin's insert default. As for
            # the cloned pages, explicit audit stamps and the save notification
            # retain history without assigning a temporary owner.
            self.sudo(reason="knowledge.vault.clone").bulk_create([vault])
            post_save.send(
                sender=self.model, instance=vault, created=True, raw=False, using=self.db, update_fields=None,
            )
        return vault.with_actor(actor)


class Vault(OwnerMixin, CreationKeyMixin, AngeeDataModel, HistoryMixin):
    """Top-level page container; the permission and namespace boundary.

    Deleting a vault cascade-deletes every page inside it; the crud delete
    mutation previews that blast radius before confirming. Ownership can be
    transferred or cleared without changing attribution; deleting a user clears
    ownership and retains their vaults. Whoever may ``share`` the vault manages
    its direct ``viewer`` shares; editor and role reach stay administrative.
    """

    runtime = True
    rebac_grantable = {"viewer": "share"}

    sqid_prefix = "vlt_"
    name = models.CharField(max_length=200)
    description = models.TextField(blank=True, default="")
    icon = models.CharField(max_length=64, blank=True, default="")
    accent = models.CharField(max_length=32, blank=True, default="")
    retrieval_class = ImplClassField(RetrievalBackend,
        default="lexical",
    )
    """Registry key for the retrieval backend this vault searches through."""

    objects = VaultManager()

    class Meta:
        """Django model options."""

        abstract = True
        ordering = ("name", "sqid")
        rebac_resource_type = "knowledge/vault"
        constraints = (
            models.UniqueConstraint(fields=("owner", "name"), name="uniq_knowledge_vault_owner_name"),
            CreationKeyMixin.creation_key_constraint(),
        )

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


class PageQuerySet(TrashQuerySet[Any], AngeeQuerySet[Any]):
    """Actor-scoped page reads; ``untrashed()`` is every page's default surface."""


class PageManager(AngeeManager.from_queryset(PageQuerySet)):  # type: ignore[misc]
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
        kind = fields.pop("kind", self.model.PageKind.NOTE)
        page_model = self.model
        if kind != self.model.PageKind.FOLDER:
            if kind not in (self.model.PageKind.NOTE, self.model.PageKind.TEMPLATE):
                raise ValueError(f"Unsupported page kind: {kind!r}")
            page_model = self.model._meta.apps.get_model("knowledge", "MarkdownPage")
            fields["kind"] = kind
        page = page_model(vault=vault, **fields)
        page.full_clean()
        page.sudo(reason="knowledge.page.create").save()
        return self.model._base_manager.get(pk=page.pk).with_actor(actor)

    def _copy_tree_in(
        self, vault: Vault, pages: Mapping[Any, Page], *, actor: SubjectRef,
    ) -> dict[Any, Page]:
        """Copy template pages within the authorized vault-cloning transaction.

        The clone owner supplies actor-readable source pages. Their unchanged
        scalar values retain source validation; tree edges are checked here and
        database constraints still apply. Insert all pages, restore their parent
        links, and batch native history before notifying the remaining subscribers.
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
            tree.prepare()
        except CycleError as error:
            raise ValidationError("Template page parents must form a tree.") from error
        copies = {
            pk: self.model(
                vault=vault,
                title=page.title,
                icon=page.icon,
                created_by_id=user_id,
                updated_by_id=user_id,
            )
            for pk, page in pages.items()
        }
        batch = list(copies.values())
        elevated = self.sudo(reason="knowledge.page.clone")
        elevated.bulk_create(batch)
        children = []
        for pk, page in copies.items():
            page.with_actor(actor)
            if pages[pk].parent_id is not None:
                page.parent = copies[pages[pk].parent_id]
                children.append(page)
        if children:
            elevated.bulk_update(children, ["parent"])
        self.model.history.db_manager(self.db).bulk_history_create(batch)
        for page in batch:
            page.skip_history_when_saving = True
            try:
                post_save.send(
                    sender=self.model, instance=page, created=True, raw=False, using=self.db, update_fields=None,
                )
            finally:
                del page.skip_history_when_saving
        return copies


class Page(TaggedModel, TrashMixin, AuditMixin, AngeeDataModel, HistoryMixin):
    """Universal addressable content node inside a vault.

    A page owns title and hierarchy. A concrete child owns each content shape;
    a parent row without a child is a folder. Trashing a page trashes every
    page below it with the same stamp, so each stored flag is exact; restoring
    it brings back the pages trashed with it. Trashed pages are withheld from
    readers who cannot delete them and leave their titles free meanwhile.
    """

    runtime = True
    rebac_grantable = {"viewer": "write"}

    sqid_prefix = "pg_"

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
    parent = models.ForeignKey(
        "self",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="children",
    )
    title = models.CharField(max_length=512, db_index=True)
    icon = models.CharField(max_length=64, blank=True, default="")

    objects = PageManager()

    class Meta:
        """Django model options."""

        abstract = True
        ordering = ("title", "sqid")
        rebac_resource_type = "knowledge/page"
        constraints = (
            models.UniqueConstraint(
                fields=("vault", "title"),
                condition=models.Q(is_trashed=False),
                name="uniq_knowledge_page_vault_title",
            ),
        )

    def __str__(self) -> str:
        """Return the page title for Django displays."""

        return self.title

    @property
    def kind(self) -> str:
        """Project the page's kind from its concrete child."""

        for child_model in concrete_child_models(self._meta.apps.get_model("knowledge", "Page")):
            child = concrete_child(self, child_model)
            if child is not None:
                if child_model is self._meta.apps.get_model("knowledge", "MarkdownPage"):
                    return str(child.kind)
                return child._meta.model_name
        return str(self.PageKind.FOLDER)

    def clean(self) -> None:
        """Keep live pages out of trashed folders."""

        super().clean()
        if self.parent_id is not None and not self.is_trashed and type(self)._base_manager.filter(
            pk=self.parent_id, is_trashed=True,
        ).exists():
            raise ValidationError({"parent": "Page parent is in the trash."})

    def trash(self, *, reason: str = "", using: str | None = None) -> None:
        """Trash this page and, with the same stamp, every untrashed page below it."""

        db = using or router.db_for_write(type(self), instance=self)
        with transaction.atomic(using=db):
            super().trash(reason=reason, using=db)
            below = self._subtree_pks(db)
            if below:
                type(self).system_queryset().using(db).filter(pk__in=below).trash(
                    reason=self.trash_reason, at=self.trashed_at,
                )

    def restore(self, *, using: str | None = None) -> None:
        """Restore this page and the pages below it that were trashed with it.

        Refuses while the containing folder is trashed, and when an untrashed
        page took a restored title meanwhile.
        """

        db = using or router.db_for_write(type(self), instance=self)
        with transaction.atomic(using=db):
            pages = type(self).system_queryset(lock=("self",)).using(db)
            if self.parent_id is not None and pages.filter(pk=self.parent_id, is_trashed=True).exists():
                raise ValidationError({"parent": "Restore the folder that contains this page first."})
            together = pages.filter(pk__in=self._subtree_pks(db, trashed_at=self.trashed_at)).trashed()
            titles = [self.title, *together.values_list("title", flat=True)]
            if pages.filter(vault_id=self.vault_id, title__in=titles).untrashed().exists():
                raise ValidationError({"title": "Another page in this vault already uses a restored title."})
            super().restore(using=db)
            together.restore()

    def _subtree_pks(self, using: str, *, trashed_at: Any = None) -> set[Any]:
        """Return the primary keys below this page, optionally only those trashed together with it."""

        pages = type(self)._base_manager.using(using)
        if trashed_at is not None:
            pages = pages.filter(is_trashed=True, trashed_at=trashed_at)
        found: set[Any] = set()
        frontier = {self.pk}
        while frontier:
            frontier = set(pages.filter(parent_id__in=frontier).values_list("pk", flat=True)) - found
            found |= frontier
        return found


class RecordBindingManager(AngeeManager):
    """Own polymorphic knowledge-to-record binding writes and reverse reads.

    Bindings are stored at :func:`rebac.generic_target` and written under the
    actor: ``permissions.zed`` requires both ends, through the target relation
    the record's app declares, for reads in either direction and for writes.
    """

    DEFAULT_ROLE = "related"
    MEMORY_ROLE = "memory"
    KNOWLEDGE_MODELS = frozenset({"knowledge.page", "knowledge.vault"})

    def ensure_page(self, record: models.Model, *, role: str, vault: Any, title: str, actor: Any) -> Any:
        """Find or create a role's page, serialized on the canonical bound record.

        The first untrashed bound page is authoritative across vaults. An unreadable
        binding is refused rather than creating a second page behind the caller's
        scope. Callers supply a vault-unique title for a newly created page.
        """

        target = generic_target(self._saved(record, "record"))
        role = self._role(role)

        def page_for_role() -> models.Model:
            with system_context(reason="knowledge.ensure_page.find"):
                binding = self.model._base_manager.filter(
                    **target.lookups(self.model, "target"), role=role, page__is_trashed=False,
                ).order_by("pk").first()
            if binding is not None:
                binding.require_access("read", actor)
                return apps.get_model("knowledge", "Page").objects.with_actor(actor).get(pk=binding.page_id)
            return apps.get_model("knowledge", "Page").objects.create_in(vault, title=title)

        with actor_context(actor):
            return cast(RecordBinding, self.upsert(target=record, page=page_for_role, role=role)).page

    def create(self, **kwargs: Any) -> models.Model:
        """Create through the idempotent role-keyed upsert contract."""

        try:
            target = kwargs.pop("target")
        except KeyError as error:
            raise TypeError("RecordBinding.objects.create() requires target.") from error
        role = kwargs.pop("role", self.DEFAULT_ROLE)
        page = kwargs.pop("page", None)
        vault = kwargs.pop("vault", None)
        if kwargs:
            unexpected = ", ".join(sorted(kwargs))
            raise TypeError(f"Unexpected RecordBinding fields: {unexpected}")
        return self.upsert(page=page, vault=vault, target=target, role=role)

    def upsert(
        self,
        *,
        target: models.Model,
        page: models.Model | Callable[[], models.Model] | None = None,
        vault: models.Model | None = None,
        role: str = DEFAULT_ROLE,
    ) -> models.Model:
        """Return one binding per knowledge owner, canonical target, and role.

        Lock and recheck the canonical target before every write, including a
        lazy page factory. Under PostgreSQL READ COMMITTED, DELETE waits for
        this transaction and its post-delete teardown sees the committed binding;
        if DELETE wins, the locked lookup refuses the now-absent target.
        """

        reference = generic_target(self._saved(target, "target"))
        with transaction.atomic():
            with system_context(reason="knowledge.record_binding.lock"):
                canonical = reference.content_type.model_class()
                lock_if_supported(canonical._base_manager.all(), no_key=True).get(pk=reference.object_id)
            owner = page() if callable(page) else page
            binding, _created = self.get_or_create(**self._key(target, page=owner, vault=vault, role=role))
            return binding

    def unbind(
        self,
        *,
        target: models.Model,
        page: models.Model | None = None,
        vault: models.Model | None = None,
        role: str = DEFAULT_ROLE,
    ) -> int:
        """Delete one role-keyed binding; its ``delete`` requires write on both ends."""

        deleted, _by_model = self.filter(**self._key(target, page=page, vault=vault, role=role)).delete()
        return deleted

    def teardown_for_record(self, record: models.Model) -> None:
        """Delete every binding to ``record`` and trash its untrashed memory pages.

        Targets declare no reverse ``GenericRelation``. The global knowledge-owned
        ``post_delete`` receiver delegates here once, on the canonical sender.
        The target's DELETE waits for :meth:`upsert` to release its row lock;
        READ COMMITTED makes that committed binding visible to teardown. Binding
        deletion is elevated and needs no live target or additional target lock.
        """

        try:
            target = generic_target(record)
        except ValueError:
            return  # An unsaved or untyped row: no binding can name it.
        # object_id is an integer column, so a row with a non-integer primary
        # key (django Session's string key, for one) can never carry bindings —
        # and coercing its pk into the filter raises on every such delete.
        if not isinstance(target.object_id, int):
            return
        bindings = self.model._base_manager.filter(**target.lookups(self.model, "target"))
        if not bindings.exists():
            return
        with system_context(reason="knowledge.record_binding.teardown"), transaction.atomic():
            pages = apps.get_model("knowledge", "Page").objects.filter(
                pk__in=bindings.filter(role=self.MEMORY_ROLE, page__isnull=False).values("page_id"),
            ).untrashed().order_by("pk")
            while page := pages.first():
                page.trash(reason="Bound record deleted.")
            bindings.delete()

    def for_record(self, record: models.Model, *, role: str | None = None) -> models.QuerySet[Any]:
        """Return actor-readable bindings on one canonical record."""

        target = generic_target(self._saved(record, "record"))
        queryset = self.get_queryset().filter(**target.lookups(self.model, "target"))
        queryset = queryset.select_related("page", "vault")
        return queryset if role is None else queryset.filter(role=self._role(role))

    def pages_for_record(self, record: models.Model, *, role: str | None = None) -> models.QuerySet[Any]:
        """Return page bindings on ``record`` without loading gated page content."""

        return self.for_record(record, role=role).filter(page__isnull=False)

    def vaults_for_record(self, record: models.Model, *, role: str | None = None) -> models.QuerySet[Any]:
        """Return vault bindings on ``record`` without loading gated vault content."""

        return self.for_record(record, role=role).filter(vault__isnull=False)

    def for_knowledge(
        self,
        knowledge: models.Model,
        *,
        role: str | None = None,
    ) -> models.QuerySet[Any]:
        """Return bindings from one readable page/vault to actor-readable targets."""

        knowledge, owner_field = self._knowledge_owner_instance(knowledge)
        queryset = self.get_queryset().filter(**{owner_field: knowledge})
        return queryset if role is None else queryset.filter(role=self._role(role))

    def records_for_page(self, page: models.Model, *, role: str | None = None) -> tuple[RecordRef, ...]:
        """Return public refs for actor-readable records bound from ``page``."""

        return tuple(binding.record_ref for binding in self.for_knowledge(page, role=role))

    def records_for_vault(self, vault: models.Model, *, role: str | None = None) -> tuple[RecordRef, ...]:
        """Return public refs for actor-readable records bound from ``vault``."""

        return tuple(binding.record_ref for binding in self.for_knowledge(vault, role=role))

    @classmethod
    def _knowledge_owner(
        cls,
        *,
        page: models.Model | None,
        vault: models.Model | None,
    ) -> tuple[models.Model, str]:
        if (page is None) == (vault is None):
            raise ValidationError("Exactly one of page or vault is required.")
        return cls._knowledge_owner_instance(page if page is not None else cast(models.Model, vault))

    @classmethod
    def _knowledge_owner_instance(cls, knowledge: models.Model) -> tuple[models.Model, str]:
        knowledge = cls._saved(knowledge, "knowledge owner")
        label = knowledge._meta.label_lower
        if label not in cls.KNOWLEDGE_MODELS:
            raise ValidationError("Knowledge bindings may originate only from a Page or Vault.")
        return knowledge, "page" if label == "knowledge.page" else "vault"

    @staticmethod
    def _saved(instance: models.Model, name: str) -> models.Model:
        if instance is None or instance.pk is None:
            raise ValidationError(f"A saved {name} is required.")
        return instance

    @staticmethod
    def _role(role: str) -> str:
        value = str(role or "").strip()
        if not value:
            raise ValidationError("A non-empty binding role is required.")
        field = RecordBinding._meta.get_field("role")
        field.run_validators(value)
        return value

    def _key(
        self,
        target: models.Model,
        *,
        page: models.Model | None,
        vault: models.Model | None,
        role: str,
    ) -> dict[str, Any]:
        """Return a binding's unique key; ``ValueError`` for a target without a REBAC type."""

        knowledge, owner_field = self._knowledge_owner(page=page, vault=vault)
        canonical = generic_target(self._saved(target, "target"))
        return {owner_field: knowledge, **canonical.lookups(self.model, "target"), "role": self._role(role)}


class RecordBinding(AuditMixin, RecordRefMixin, AngeeDataModel):
    """Role-keyed edge from a knowledge Page/Vault to any REBAC record.

    The target is canonicalized to its topmost REBAC-typed MTI ancestor. The
    edge grants access to neither side. Its REBAC permissions require both the
    knowledge row and the target, through the relation the target's app
    declares; a target type without one has unreadable bindings, including for
    administrators, and cannot be bound under an actor.
    """

    merge_policy = MergePolicy.MOVE
    merge_identity = (("page", "content_type", "object_id", "role"), ("vault", "content_type", "object_id", "role"))
    runtime = True
    sqid_prefix = "krb_"

    page = models.ForeignKey(
        "knowledge.Page",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="record_bindings",
    )
    vault = models.ForeignKey(
        "knowledge.Vault",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="record_bindings",
    )
    content_type = models.ForeignKey(ContentType, on_delete=models.CASCADE, related_name="+")
    object_id = models.PositiveBigIntegerField()
    target = GenericForeignKey("content_type", "object_id")
    role = models.SlugField(max_length=64, default=RecordBindingManager.DEFAULT_ROLE)

    objects = RecordBindingManager()

    class Meta:
        """Django model options for knowledge record bindings."""

        abstract = True
        ordering = ("role", "sqid")
        rebac_resource_type = "knowledge/record_binding"
        constraints = (
            models.CheckConstraint(
                condition=(
                    models.Q(page__isnull=False, vault__isnull=True) | models.Q(page__isnull=True, vault__isnull=False)
                ),
                name="ck_knowledge_record_binding_owner",
            ),
            models.UniqueConstraint(
                fields=("page", "content_type", "object_id", "role"),
                condition=models.Q(page__isnull=False),
                name="uq_knowledge_record_binding_page_target_role",
            ),
            models.UniqueConstraint(
                fields=("vault", "content_type", "object_id", "role"),
                condition=models.Q(vault__isnull=False),
                name="uq_knowledge_record_binding_vault_target_role",
            ),
        )
        indexes = (models.Index(fields=("content_type", "object_id", "role")),)

    def clean(self) -> None:
        """Require exactly one knowledge owner."""

        super().clean()
        if (self.page_id is None) == (self.vault_id is None):
            raise ValidationError("Exactly one of page or vault is required.")

    def __str__(self) -> str:
        """Return a readable knowledge-to-record edge label."""

        owner = f"page:{self.page_id}" if self.page_id is not None else f"vault:{self.vault_id}"
        return f"{owner}->{self.record_model_label}:{self.record_public_id}#{self.role}"


class StaleBodyError(ValueError):
    """Raised when a body write expects a hash the stored body no longer has."""


class UnsupportedPageKindError(ValueError):
    """Raised when a body write targets a page without a markdown child."""


class StructuredEditError(ValueError):
    """Base for a structure-aware markdown edit that cannot be applied."""


class SectionNotFoundError(StructuredEditError):
    """Raised when a heading path (or replace target) matches nothing."""


class AmbiguousMatchError(StructuredEditError):
    """Raised when a heading path (or replace target) matches more than once."""


class MarkdownPageManager(AngeeManager):
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
        for body in copies:
            post_save.send(
                sender=self.model, instance=body, created=True,
                raw=False, using=self.db, update_fields=None, knowledge_backlinks_rebuilt=True,
            )

    def write_body(self, page: Any, body: str, *, expected_hash: str | None = None) -> Any:
        """Update ``page``'s markdown body, last-write-wins.

        ``expected_hash`` is an optimistic-concurrency token: when supplied
        and the stored ``body_hash`` differs, the write is rejected with
        :class:`StaleBodyError` so the caller can reload and retry.
        """

        if page.kind == Page.PageKind.FOLDER:
            raise UnsupportedPageKindError(f"Pages of kind {page.kind!r} carry no markdown body.")
        with transaction.atomic():
            markdown = self.select_for_update().get(pk=page.pk)
            if expected_hash is not None and expected_hash != markdown.body_hash:
                raise StaleBodyError("Body hash is stale; reload the page and retry.")
            markdown.body = body
            markdown.save(update_fields=("body",))
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

        markdown = self.filter(pk=page.pk).first()
        return "" if markdown is None else markdown.body


class SectionOp(StrEnum):
    """How a markdown section is spliced; member names are GraphQL wire values."""

    REPLACE = "replace"
    APPEND = "append"
    PREPEND = "prepend"


class MarkdownPage(RevisionMixin, models.Model, metaclass=RebacModelBase):
    """Concrete page with a versioned markdown body.

    ``body`` is the canonical content store; ``body_hash`` and
    ``word_count`` are derived on save. Body edits are versioned through
    ``revisions`` so they can be rolled back.
    """

    runtime = True
    extends = "knowledge.Page"

    revisioned_fields = ("body",)

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
        if reversion.is_active():
            # django-reversion follows MTI parent links when reading field_dict.
            reversion.add_to_revision(self.page_ptr)


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

        self.rebuild_many((markdown,))

    def rebuild_many(self, bodies: Iterable[MarkdownPage]) -> None:
        """Replace a batch's wikilinks, resolving titles once per source vault."""

        batch = list(bodies)
        if not batch:
            return
        page_model = apps.get_model("knowledge", "Page")
        vault_ids = {body.vault_id for body in batch}
        links = self.model._base_manager
        with system_context(reason="knowledge.backlinks"), transaction.atomic():
            resolved = {
                (vault_id, title): pk
                for vault_id, title, pk in page_model._base_manager.filter(vault_id__in=vault_ids)
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


class Link(AngeeDataModel):
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

    def __str__(self) -> str:
        """Return the link target text for Django displays."""

        return self.target_text
