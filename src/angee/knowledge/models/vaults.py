"""Knowledge vaults and their native persistence owners."""

from __future__ import annotations

from contextlib import nullcontext
from itertools import islice
from typing import Any, ClassVar, cast

from django.apps import apps
from django.core.exceptions import ValidationError
from django.db import models, transaction
from django.db.models.signals import post_save
from rebac import (
    PermissionDenied,
    SubjectRef,
    check_new,
    current_actor,
    to_subject_ref,
)
from rebac.errors import MissingActorError
from rebac.resources import model_resource_type

from angee.base.fields.implementation import ImplClassField
from angee.base.lineage import concrete_child_accessor, concrete_child_models
from angee.base.models.base import AngeeDataModel
from angee.base.models.ownership import OwnerMixin
from angee.base.models.trash import TrashMixin, TrashQuerySet
from angee.base.permissions import actor_user_id
from angee.knowledge.exceptions import UnsupportedPageKindError
from angee.knowledge.retrieval import MAX_SEARCH_PAGE_SIZE, RetrievalBackend
from angee.provenance.context import changeset, changeset_model, require_changeset, write_group
from angee.provenance.contrib.angee.history import HistoryMixin
from angee.provenance.contrib.angee.managers import AngeeHistoryManager
from angee.provenance.contrib.angee.querysets import AngeeHistoryQuerySet

MAX_SEARCH_VAULTS = 100


class VaultQuerySet[VaultT: Vault](TrashQuerySet[VaultT], AngeeHistoryQuerySet[VaultT]):
    """Actor-scoped vault reads and search helpers."""

    def search_pages(self, query: str, *, first: int = 20) -> list[Any]:
        """Search at most 100 readable vaults, once per selected backend class.

        Vault name/id order bounds the work set. Backend-key order is stable;
        each backend owns ranking within its group and the page budget is total.
        """
        limit = max(0, min(first, MAX_SEARCH_PAGE_SIZE))
        if not limit:
            return []
        groups: dict[str, list[Any]] = {}
        for vault in self.untrashed().scoped().order_by("name", "sqid")[:MAX_SEARCH_VAULTS]:
            groups.setdefault(vault.retrieval_class, []).append(vault)
        field = self.model.retrieval_class.field
        pages: list[Any] = []
        for key, vaults in sorted(groups.items()):
            remaining = limit - len(pages)
            implementation = field.resolve_class(key)
            pages.extend(islice(implementation.search_many(vaults, query, first=remaining), remaining))
            if len(pages) == limit:
                break
        return pages


_VaultManagerBase = AngeeHistoryManager.from_queryset(VaultQuerySet)


class VaultManager[VaultT: Vault](_VaultManagerBase[VaultT]):
    """Factories for actor-owned vault writes."""

    def for_resource(self, reference: str) -> VaultT:
        """Resolve a declared resource identity to its current readable, live vault."""

        ledger = apps.get_model("resources", "Resource")
        target = ledger.objects.resolve(reference, model=self.model)
        vault = self.untrashed().filter(pk=target.pk).first()
        if vault is None:
            raise ValueError("The notes vault is unavailable.")
        return vault

    def create_for(self, owner: Any, **fields: Any) -> VaultT:
        """Create a vault owned by ``owner`` after the REBAC create preflight.

        ``owner`` must be the acting user — ownership on behalf of someone
        else is refused so the row the gate authorized is the row written.
        """

        actor = current_actor()
        resource_type = model_resource_type(self.model)
        if actor is None:
            raise MissingActorError(f"Creating {resource_type or self.model._meta.label_lower} requires an actor.")
        if resource_type is None or not check_new(
            subject=actor,
            action="create",
            resource_type=resource_type,
        ).allowed:
            raise PermissionDenied(f"Denied: {actor} cannot create {resource_type or self.model._meta.label_lower}")
        if owner is None or to_subject_ref(owner) != actor:
            raise PermissionDenied(f"Denied: {actor} cannot create a vault owned by {owner!r}")
        return self._create_for_actor(actor, **fields)

    def create_from(
        self,
        template: Vault,
        *,
        name: str,
        owned: bool = True,
        idempotency_key: str | None = None,
    ) -> VaultT:
        """Clone a readable vault's page tree and markdown bodies for the actor.

        Requires vault create and, for a new clone, template read. New identities and attribution
        belong to the actor; grants and record bindings are never copied. Unknown
        page kinds are refused rather than copied without their content.
        ``owned=False`` inserts the clone without an owner. An idempotency key
        belongs to the creating actor, even after ownership is transferred or
        cleared; changing template, name, or ownership intent conflicts. Template
        edits, loss of access, or deletion do not change a completed clone.
        """

        actor = current_actor()
        resource_type = model_resource_type(self.model)
        if actor is None or resource_type is None or not check_new(
            subject=actor,
            action="create",
            resource_type=resource_type,
        ).allowed:
            raise PermissionDenied(f"Denied: {actor} cannot create {resource_type or self.model._meta.label_lower}")
        user_id = actor_user_id(actor)
        if user_id is None:
            raise PermissionDenied("Cloning a vault requires a user actor.")
        claim_values = [template.pk, name, owned]

        def insert() -> VaultT:
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
                if page.kind not in (
                    page_model.PageKind.NOTE, page_model.PageKind.TEMPLATE, page_model.PageKind.FOLDER,
                ):
                    raise UnsupportedPageKindError(f"Cannot clone pages of kind {page.kind!r}.")
            bodies = list(markdown_model._default_manager.with_actor(actor).filter(pk__in=pages))
            if {body.pk for body in bodies} != {
                page.pk for page in pages.values() if page.kind != page_model.PageKind.FOLDER
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
            )
            copies = page_model._default_manager._copy_tree_in(vault, pages, actor=actor)
            markdown_model._default_manager._copy_bodies(bodies, copies, actor=actor)
            return vault

        claim_boundary = changeset(fresh=True) if idempotency_key is not None else nullcontext()
        with claim_boundary, transaction.atomic():
            if idempotency_key is not None:
                vault = changeset_model().objects.claim_created(
                    f"knowledge.vault.clone:{user_id}",
                    idempotency_key,
                    values=claim_values,
                    model=self.model,
                    unavailable=ValidationError(
                        {"idempotency_key": "The claimed clone result is unavailable."},
                    ),
                )
                if vault is not None:
                    return vault.with_actor(actor)
            vault = insert()
            if idempotency_key is not None:
                require_changeset().record_created(vault)
            return vault.with_actor(actor)

    def _create_for_actor(self, actor: SubjectRef, *, owned: bool = True, **fields: Any) -> VaultT:
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
            # Native bulk insertion bypasses OwnerMixin's insert default.
            # Notify lifecycle subscribers within the captured write's group.
            with write_group():
                self.sudo(reason="knowledge.vault.clone").bulk_create([vault])
                post_save.send(
                    sender=self.model, instance=vault, created=True, raw=False, using=self.db, update_fields=None,
                )
        return vault.with_actor(actor)


class Vault(OwnerMixin, TrashMixin, HistoryMixin, AngeeDataModel, concrete=True):
    """Top-level page container; the permission and namespace boundary.

    Trashing a vault withholds its pages while retaining their independent trash
    flags, so restoring the vault never restores a separately removed page. Ownership can be
    transferred or cleared without changing attribution; deleting a user clears
    ownership and retains their vaults. Whoever may ``share`` the vault manages
    its direct ``viewer`` shares; editor and role reach stay administrative.
    """

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
        constraints: ClassVar[list[models.BaseConstraint] | tuple[models.BaseConstraint, ...]] = (
            models.UniqueConstraint(fields=("owner", "name"), name="uniq_knowledge_vault_owner_name"),
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
        internals. The native declared field owns value normalization and the
        trusted registry resolver owns class loading.
        """

        backend_class = cast(
            ImplClassField[RetrievalBackend], type(self)._meta.get_field("retrieval_class"),
        ).resolve_class(key)
        return backend_class(self)
