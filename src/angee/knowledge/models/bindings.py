"""Knowledge bindings and their native persistence owners."""

from __future__ import annotations

from contextlib import nullcontext
from typing import TYPE_CHECKING, Any, Self, cast

from django.contrib.contenttypes.fields import GenericForeignKey
from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import ValidationError
from django.db import models, router, transaction
from django.utils import timezone
from rebac import (
    PermissionDenied,
    current_actor,
    database_context,
    generic_target,
    system_context,
)

from angee.base.models.audit import AuditMixin
from angee.base.models.base import AngeeDataModel
from angee.base.models.merge_references import MergePolicy
from angee.base.models.refs import RecordRefMixin
from angee.base.models.trash import TrashMixin
from angee.base.permissions import actor_user_id
from angee.base.querysets import row_lock_paths
from angee.base.refs import RecordRef
from angee.provenance.context import changeset, changeset_model, require_changeset
from angee.provenance.contrib.angee.history import HistoryMixin
from angee.provenance.contrib.angee.managers import AngeeHistoryManager
from angee.provenance.contrib.angee.querysets import AngeeHistoryQuerySet

if TYPE_CHECKING:
    from angee.knowledge.models.vaults import Vault


class RecordBindingQuerySet[BindingT: RecordBinding](AngeeHistoryQuerySet[BindingT]):
    """Knowledge binding selections retaining the selected population and actor."""

    def for_record(self, record: models.Model, *, role: str | None = None) -> Self:
        """Return actor-readable bindings on one canonical record."""

        target = generic_target(self.model.require_saved(record, "record"))
        queryset = self.filter(**target.lookups(self.model, "target"))
        queryset = queryset.select_related("page", "vault")
        return queryset if role is None else queryset.filter(role=self.model.normalize_role(role))

    def pages_for_record(self, record: models.Model, *, role: str | None = None) -> Self:
        """Return page bindings on ``record`` without loading gated page content."""

        return self.for_record(record, role=role).filter(page__isnull=False)

    def vaults_for_record(self, record: models.Model, *, role: str | None = None) -> Self:
        """Return vault bindings on ``record`` without loading gated vault content."""

        return self.for_record(record, role=role).filter(vault__isnull=False)

    def for_knowledge(
        self,
        knowledge: models.Model,
        *,
        role: str | None = None,
    ) -> Self:
        """Return bindings from one readable page/vault to actor-readable targets."""

        knowledge, owner_field = self.model.knowledge_owner_instance(knowledge)
        queryset = self.filter(**{owner_field: knowledge})
        return queryset if role is None else queryset.filter(role=self.model.normalize_role(role))

    def records_for_page(self, page: models.Model, *, role: str | None = None) -> tuple[RecordRef, ...]:
        """Return public refs for actor-readable records bound from ``page``."""

        return tuple(binding.record_ref for binding in self.for_knowledge(page, role=role))

    def records_for_vault(self, vault: models.Model, *, role: str | None = None) -> tuple[RecordRef, ...]:
        """Return public refs for actor-readable records bound from ``vault``."""

        return tuple(binding.record_ref for binding in self.for_knowledge(vault, role=role))


_RecordBindingManagerBase = AngeeHistoryManager.from_queryset(RecordBindingQuerySet)


class RecordBindingManager[BindingT: RecordBinding](_RecordBindingManagerBase[BindingT]):
    """Own polymorphic knowledge-to-record binding writes and reverse reads.

    Bindings are stored at :func:`rebac.generic_target` and written under the
    actor: ``permissions.zed`` requires both ends, through the target relation
    the record's app declares, for reads in either direction and for writes.
    """

    DEFAULT_ROLE = "related"

    def create_note(
        self, *, target: models.Model, vault: Vault, body: str,
        role: str = DEFAULT_ROLE, idempotency_key: str | None = None,
    ) -> BindingT:
        """Atomically create and bind a markdown note, replaying the actor's receipt."""

        actor = current_actor()
        user_id = actor_user_id(actor)
        if user_id is None:
            raise PermissionDenied("Creating a note requires a user actor.")
        body = body.strip()
        if not body:
            raise ValidationError("Write a note before posting.")
        role = self.model.normalize_role(role)
        target = self.model.require_saved(target, "target")
        using = self._db or router.db_for_write(self.model, instance=target)
        bindings = self.db_manager(using, hints={"instance": target})
        boundary = changeset(fresh=True, using=using) if idempotency_key is not None else nullcontext()
        with database_context(using), boundary, transaction.atomic(using=using):
            reference = generic_target(target)
            claim_values = [reference.content_type.pk, reference.object_id, vault.pk, role, body]
            if idempotency_key is not None:
                binding = changeset_model().objects.db_manager(using).claim_created(
                    f"knowledge.record_note:{user_id}",
                    idempotency_key,
                    values=claim_values,
                    model=self.model,
                    unavailable=ValidationError("The claimed note is unavailable."),
                )
                if binding is not None:
                    return binding.with_actor(actor)
            page_model = self.model._meta.apps.get_model("knowledge", "Page")
            title = f"{body.splitlines()[0][:100]} · {timezone.now().isoformat()}"
            page = page_model.objects.db_manager(using, hints={"instance": vault}).create_in(
                vault, title=title, body=body,
            )
            binding = bindings.upsert(target=target, page=page, role=role)
            if idempotency_key is not None:
                require_changeset().record_created(binding)
            return binding

    def create(self, **kwargs: Any) -> BindingT:
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
    ) -> BindingT:
        """Return one binding per knowledge owner, canonical target, and role.

        Lock and recheck the canonical target before every write. If target
        deletion wins, the locked lookup refuses the now-absent target.
        """

        target = self.model.require_saved(target, "target")
        using = self._db or router.db_for_write(self.model, instance=target)
        bindings = self.db_manager(using, hints={"instance": target})
        with database_context(using), transaction.atomic(using=using):
            reference = generic_target(target)
            with system_context(reason="knowledge.record_binding.lock"):
                canonical = reference.content_type.model_class()
                if canonical is None:
                    raise ValueError("Knowledge binding target model is unavailable.")
                canonical._base_manager.db_manager(using, hints={"instance": target}).select_for_update(
                    of=row_lock_paths(canonical), no_key=True,
                ).get(
                    pk=reference.object_id
                )
            owner = page
            if owner is not None:
                # Serialize binding admission with page trash.
                with system_context(reason="knowledge.record_binding.page_lock"):
                    owner = (
                        type(owner)
                        ._base_manager.db_manager(using, hints={"instance": owner})
                        .select_for_update(of=row_lock_paths(type(owner)), no_key=True)
                        .get(pk=owner.pk)
                    )
                    assert isinstance(owner, TrashMixin)
                    if owner.is_trashed:
                        raise ValidationError("Cannot bind a page in the trash.")
            binding, _created = bindings.get_or_create(**self._key(target, page=owner, vault=vault, role=role))
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

    def _key(
        self,
        target: models.Model,
        *,
        page: models.Model | None,
        vault: models.Model | None,
        role: str,
    ) -> dict[str, Any]:
        """Return a binding's unique key; ``ValueError`` for a target without a REBAC type."""

        knowledge, owner_field = self.model.knowledge_owner(page=page, vault=vault)
        canonical = generic_target(self.model.require_saved(target, "target"))
        return {
            owner_field: knowledge, **canonical.lookups(self.model, "target"),
            "role": self.model.normalize_role(role),
        }


class RecordBinding(AuditMixin, RecordRefMixin, HistoryMixin, AngeeDataModel, concrete=True):
    """Role-keyed edge from a knowledge Page/Vault to any REBAC record.

    The target is canonicalized to its topmost REBAC-typed MTI ancestor. The
    edge grants access to neither side. Its REBAC permissions require both the
    knowledge row and the target, through the relation the target's app
    declares; a target type without one has unreadable bindings, including for
    administrators, and cannot be bound under an actor.
    """

    history_revert_permission = "delete"
    merge_policy = MergePolicy.MOVE
    on_target_delete = models.CASCADE
    merge_identity = (("page", "content_type", "object_id", "role"), ("vault", "content_type", "object_id", "role"))
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

    @classmethod
    def knowledge_owner(
        cls,
        *,
        page: models.Model | None,
        vault: models.Model | None,
    ) -> tuple[models.Model, str]:
        if (page is None) == (vault is None):
            raise ValidationError("Exactly one of page or vault is required.")
        return cls.knowledge_owner_instance(page if page is not None else cast(models.Model, vault))

    @classmethod
    def knowledge_owner_instance(cls, knowledge: models.Model) -> tuple[models.Model, str]:
        knowledge = cls.require_saved(knowledge, "knowledge owner")
        label = knowledge._meta.label_lower
        if label not in frozenset({"knowledge.page", "knowledge.vault"}):
            raise ValidationError("Knowledge bindings may originate only from a Page or Vault.")
        return knowledge, "page" if label == "knowledge.page" else "vault"

    @staticmethod
    def require_saved(instance: models.Model, name: str) -> models.Model:
        if instance is None or instance.pk is None:
            raise ValidationError(f"A saved {name} is required.")
        return instance

    @classmethod
    def normalize_role(cls, role: str) -> str:
        value = str(role or "").strip()
        if not value:
            raise ValidationError("A non-empty binding role is required.")
        field = cls._meta.get_field("role")
        field.run_validators(value)
        return value

    def clean(self) -> None:
        """Require exactly one knowledge owner."""

        super().clean()
        if (self.page_id is None) == (self.vault_id is None):
            raise ValidationError("Exactly one of page or vault is required.")

    def __str__(self) -> str:
        """Return a readable knowledge-to-record edge label."""

        owner = f"page:{self.page_id}" if self.page_id is not None else f"vault:{self.vault_id}"
        return f"{owner}->{self.record_model_label}:{self.record_public_id}#{self.role}"
