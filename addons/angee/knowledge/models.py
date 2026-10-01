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
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from enum import StrEnum
from graphlib import CycleError, TopologicalSorter
from typing import Any, ClassVar, cast

import reversion
from django.apps import apps
from django.contrib.contenttypes.fields import GenericForeignKey
from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import ValidationError
from django.db import models, transaction
from django.db.models.signals import post_save
from markdown_it import MarkdownIt
from rebac import (
    MissingActorError,
    ObjectRef,
    PermissionDenied,
    SubjectRef,
    current_actor,
    system_context,
    to_subject_ref,
)
from rebac.backends import backend as rebac_backend
from rebac.mixins import RebacModelBase
from rebac.resources import model_resource_type

from angee.base.actors import actor_user_id
from angee.base.fields import StateField
from angee.base.impl import ImplClassField
from angee.base.mixins import AuditMixin, CreationKeyMixin, CreationKeyQuerySet, HistoryMixin, OwnerMixin, RevisionMixin
from angee.base.models import AngeeDataModel, AngeeManager, AngeeQuerySet
from angee.base.refs import RecordRef, RecordRefMixin, canonical_record_target, concrete_child, concrete_child_models
from angee.knowledge.retrieval import RetrievalBackend

_WIKILINK_RE = re.compile(r"\[\[([^\[\]\n]+?)\]\]")
logger = logging.getLogger(__name__)

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
        sidecar kinds are refused rather than copied without their content.
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
                for page in page_model._default_manager.with_actor(actor).filter(vault=source).order_by("pk")
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


class Vault(AuditMixin, OwnerMixin, CreationKeyMixin, AngeeDataModel, HistoryMixin):
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
        database constraints still apply. Save notifications preserve native
        history and other subscribers after each level's bulk insert.
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
        copies: dict[Any, Page] = {}
        while tree.is_active():
            ready = tree.get_ready()
            batch = [
                self.model(
                    vault=vault,
                    parent=copies[pages[pk].parent_id] if pages[pk].parent_id is not None else None,
                    title=pages[pk].title,
                    icon=pages[pk].icon,
                    created_by_id=user_id,
                    updated_by_id=user_id,
                )
                for pk in ready
            ]
            self.sudo(reason="knowledge.page.clone").bulk_create(batch)
            for pk, page in zip(ready, batch, strict=True):
                copies[pk] = page.with_actor(actor)
                post_save.send(
                    sender=self.model, instance=page, created=True, raw=False, using=self.db, update_fields=None,
                )
            tree.done(*ready)
        return copies


class Page(AuditMixin, AngeeDataModel, HistoryMixin):
    """Universal addressable content node inside a vault.

    A page owns title and hierarchy. A concrete child owns each content shape;
    a parent row without a child is a folder.
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
        constraints = (models.UniqueConstraint(fields=("vault", "title"), name="uniq_knowledge_page_vault_title"),)

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


class RecordBindingManager(AngeeManager):
    """Own polymorphic knowledge-to-record binding writes and reverse reads.

    Record owners contribute two-ended read permissions through reverse generic
    relations. Both read directions use that same REBAC scope; writes separately
    require permission on the knowledge owner and canonical target.
    """

    DEFAULT_ROLE = "related"
    KNOWLEDGE_MODELS = frozenset({"knowledge.page", "knowledge.vault"})

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
        page: models.Model | None = None,
        vault: models.Model | None = None,
        role: str = DEFAULT_ROLE,
    ) -> models.Model:
        """Return one binding per knowledge owner, canonical target, and role."""

        knowledge, owner_field = self._knowledge_owner(page=page, vault=vault)
        canonical = canonical_record_target(self._saved(target, "target"))
        cast(Any, knowledge).require_access("write")
        self._require_target_access(
            canonical,
            "write",
            "Write access to the target is required to bind knowledge.",
        )
        actor = self._actor()
        role = self._role(role)
        lookup = {
            owner_field: knowledge,
            "content_type": canonical.content_type,
            "object_id": canonical.object_id,
            "role": role,
        }
        with system_context(reason="knowledge.record_binding.upsert"), transaction.atomic():
            binding, _created = self.model._base_manager.get_or_create(**lookup)
        return binding.with_actor(actor)

    def unbind(
        self,
        *,
        target: models.Model,
        page: models.Model | None = None,
        vault: models.Model | None = None,
        role: str = DEFAULT_ROLE,
    ) -> int:
        """Delete one role-keyed binding after both owners authorize the write."""

        knowledge, owner_field = self._knowledge_owner(page=page, vault=vault)
        canonical = canonical_record_target(self._saved(target, "target"))
        cast(Any, knowledge).require_access("write")
        self._require_target_access(
            canonical,
            "write",
            "Write access to the target is required to unbind knowledge.",
        )
        with system_context(reason="knowledge.record_binding.unbind"):
            deleted, _by_model = self.model._base_manager.filter(
                **{
                    owner_field: knowledge,
                    "content_type": canonical.content_type,
                    "object_id": canonical.object_id,
                    "role": self._role(role),
                }
            ).delete()
        return deleted

    def teardown_for_record(self, record: models.Model) -> None:
        """Delete every binding to ``record`` before the target row disappears.

        Targets may omit a reverse ``GenericRelation``. The global knowledge-owned
        ``pre_delete`` receiver therefore delegates here so primary-key reuse cannot
        make an old binding resolve to a new row. Deleting the bindings normally keeps
        their normal ``post_delete`` lifecycle intact.
        """

        if record.pk is None:
            return
        content_type, object_id = canonical_record_target(record)
        # object_id is an integer column, so a row with a non-integer primary
        # key (django Session's string key, for one) can never carry bindings —
        # and coercing its pk into the filter raises on every such delete.
        if not isinstance(object_id, int):
            return
        bindings = self.model._base_manager.filter(content_type=content_type, object_id=object_id)
        if not bindings.exists():
            return
        with system_context(reason="knowledge.record_binding.teardown"), transaction.atomic():
            bindings.delete()

    def for_record(self, record: models.Model, *, role: str | None = None) -> models.QuerySet[Any]:
        """Return actor-readable bindings on one canonical record."""

        canonical = canonical_record_target(self._saved(record, "record"))
        queryset = self.get_queryset().filter(
            content_type=canonical.content_type, object_id=canonical.object_id
        )
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

    @staticmethod
    def _actor() -> Any:
        actor = current_actor()
        if actor is None:
            raise MissingActorError("Knowledge record bindings require an actor.")
        return actor

    @classmethod
    def _require_target_access(cls, canonical: Any, action: str, message: str) -> None:
        if (
            not rebac_backend()
            .check_access(
                subject=cls._actor(),
                action=action,
                resource=_canonical_object_ref(canonical.content_type, canonical.object_id),
            )
            .allowed
        ):
            raise PermissionDenied(message)


class RecordBinding(AuditMixin, RecordRefMixin, AngeeDataModel):
    """Role-keyed edge from a knowledge Page/Vault to any REBAC record.

    The target is canonicalized to its topmost REBAC-typed MTI ancestor. The
    edge grants access to neither side. Its REBAC read permission requires both
    the knowledge row and target to be readable. A target whose owner contributes
    no permission arm has unreadable bindings, including for administrators.
    """

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
        """Require exactly one knowledge owner and a REBAC-typed target."""

        super().clean()
        if (self.page_id is None) == (self.vault_id is None):
            raise ValidationError("Exactly one of page or vault is required.")
        content_type = cast(ContentType, self.content_type)
        _canonical_object_ref(content_type, self.object_id)

    def __str__(self) -> str:
        """Return a readable knowledge-to-record edge label."""

        owner = f"page:{self.page_id}" if self.page_id is not None else f"vault:{self.vault_id}"
        return f"{owner}->{self.record_model_label}:{self.record_public_id}#{self.role}"


def _canonical_object_ref(content_type: ContentType, object_id: Any) -> ObjectRef:
    """Return the REBAC identity stored by a canonical record pointer."""

    model = content_type.model_class()
    resource_type = None if model is None else model_resource_type(model)
    if model is None or resource_type is None:
        raise ValidationError("Knowledge bindings require a REBAC-typed target.")
    return ObjectRef(resource_type, str(object_id))


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
        """Insert child bodies for pages admitted by the vault clone preflight."""

        for source in bodies:
            page = pages[source.pk]
            body = self.model(
                page_ptr=page,
                kind=source.kind,
                body=source.body,
                body_hash=source.body_hash,
                word_count=source.word_count,
            )
            with system_context(reason="knowledge.vault.clone.markdown"):
                body.save_base(raw=True, force_insert=True, using=self.db)
            post_save.send(
                sender=self.model, instance=body.with_actor(actor), created=True,
                raw=False, using=self.db, update_fields=None,
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
        vault_ids = {body.page_ptr.vault_id for body in batch}
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
                    target_id = resolved.get((body.page_ptr.vault_id, target))
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
