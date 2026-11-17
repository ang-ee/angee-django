"""Tags: a polymorphic shared labelling vocabulary.

A :class:`Tag` is one label in a vocabulary; a :class:`TagAssignment` is the
polymorphic edge attaching a tag to a record of a taggable type: a
``content_type``/``object_id`` pair with a
:class:`~django.contrib.contenttypes.fields.GenericForeignKey` ``target``, stored
at :func:`rebac.generic_target`. Tags reach every taggable model without a FK
back to it. A :class:`TagCategory` groups tags in a manual order and may cap how
many of its tags one record holds; a tag sits in at most one category, and an
uncategorised tag stays valid.

**Scope.** Tags and their categories are reference vocabulary readable by every
non-anonymous actor through the native ``authenticated`` permission; tag
administrators curate it.
A tag assignment is authorized by the record it tags (``permissions.zed``):
adding or removing a tag needs the record owner's declared tag authority, and an
assignment is visible to whoever reads both the tag and the record.

**A taggable type is declared by its owner.** The owning addon depends on
``angee.tags``, composes :class:`TaggedModel` onto the model (the reverse
accessor — a private field, no column, no migration — that also lets the delete
collector cascade the edges, and the ``tags`` input consumer), declares the
type's ``target``-backed relation on ``tags/tag_assignment`` in its
``permissions.extends.zed``, composes :class:`angee.tags.schema.TaggedNode` onto
the model's console node type and :func:`angee.tags.schema.tags_input_extensions`
onto its console insert and set inputs, and places the ``tags`` field on its
record form and list. A form then edits tags like any field and the row's save
writes them. :meth:`TagAssignment.check` keeps the composed models and the
declared relations in step. The tags addon never names another addon's model.
Compose :class:`TaggedModel` on the topmost REBAC-typed MTI ancestor the
canonical edge keys on, never on a child, so the delete collector filters at the
same content type the write used (the placement invariant in
:mod:`angee.base.refs`).
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from operator import attrgetter
from typing import Any, Self

from django.apps import apps
from django.contrib.contenttypes.fields import GenericForeignKey, GenericRelation
from django.contrib.contenttypes.models import ContentType
from django.core import checks
from django.core.exceptions import NON_FIELD_ERRORS, ValidationError
from django.db import models, router, transaction
from rebac import GenericTarget, generic_target
from rebac.field_backing import canonical_model
from rebac.resources import model_for_resource_type, model_resource_type

from angee.base.fields.ordering import FractionalRankField
from angee.base.fields.text import ColorField
from angee.base.models.audit import AuditMixin
from angee.base.models.base import AngeeDataModel
from angee.base.models.identity import instance_from_public_id
from angee.base.models.inputs import InputExtensionsMixin
from angee.base.models.merge import MergeableMixin
from angee.base.models.merge_references import MergePolicy
from angee.base.models.refs import RecordRefMixin
from angee.base.models.roles import role_anchor
from angee.base.models.trash import ArchiveMixin, ArchiveQuerySet
from angee.base.permissions import declared_rebac_definition
from angee.base.querysets import MergeableQuerySet, row_lock_paths
from angee.provenance.contrib.angee.history import HistoryMixin
from angee.provenance.contrib.angee.managers import AngeeHistoryManager, AngeeHistoryMergeableUnscopedManager
from angee.provenance.contrib.angee.querysets import AngeeHistoryQuerySet


class TagCategoryQuerySet[CategoryT: TagCategory](ArchiveQuerySet[CategoryT], AngeeHistoryQuerySet[CategoryT]):
    """Archive read scopes layered over the REBAC-scoped category queryset."""


TagCategoryManager = AngeeHistoryManager.from_queryset(TagCategoryQuerySet)


class TagCategory(HistoryMixin, ArchiveMixin, AngeeDataModel, concrete=True):
    """A named, manually ordered group of tags, shared like the tags themselves.

    Every taggable type can carry a category's tags; a category narrows no model.
    ``max_per_record`` caps how many of its tags one record holds, which
    :meth:`TagAssignmentManager.require_category_maximum` judges on every write
    that can raise a record's count. Deleting a category leaves its tags
    uncategorised.
    """

    sqid_prefix = "tgc_"

    name = models.CharField(max_length=128)
    description = models.TextField(blank=True, default="")
    position = FractionalRankField()
    max_per_record = models.PositiveSmallIntegerField(null=True, blank=True)
    """The most tags of this category one record may hold; ``None`` sets no maximum."""

    objects = TagCategoryManager()

    class Meta:
        """Django model options for a tag category."""

        abstract = True
        ordering = ("position", "name", "sqid")
        rebac_resource_type = "tags/tag_category"
        verbose_name_plural = "tag categories"
        constraints = (
            models.UniqueConstraint(fields=("position",), name="%(app_label)s_category_position"),
            models.CheckConstraint(
                condition=models.Q(max_per_record__isnull=True) | models.Q(max_per_record__gte=1),
                name="%(app_label)s_category_max_per_record_positive",
                violation_error_message="A category's maximum per record is at least one tag.",
            ),
        )

    def __str__(self) -> str:
        """Return the category name for Django displays."""

        return self.name

    def save(self, *args: Any, **kwargs: Any) -> None:
        """Save the category; a written maximum must already hold on every record it tags.

        Lowering the maximum below what a record holds is refused, and the save
        rolls back.
        """

        update_fields = kwargs.get("update_fields")
        judged = (
            not self._state.adding
            and self.max_per_record is not None
            and (update_fields is None or "max_per_record" in update_fields)
        )
        with transaction.atomic(using=kwargs.get("using") or router.db_for_write(type(self), instance=self)):
            super().save(*args, **kwargs)
            if judged:
                assignment_model = apps.get_model(self._meta.app_label, "TagAssignment")
                assert issubclass(assignment_model, TagAssignment)
                assignment_model.objects.require_category_maximum(
                    models.Q(tag__category=self.pk),
                    field="max_per_record",
                )


class TagQuerySet[TagT: Tag](
    MergeableQuerySet[TagT],
    ArchiveQuerySet[TagT],
    AngeeHistoryQuerySet[TagT],
):
    """Archive read scopes layered over the REBAC-scoped tag queryset."""


TagManager = AngeeHistoryManager.from_queryset(TagQuerySet)


class Tag(HistoryMixin, MergeableMixin, ArchiveMixin, AngeeDataModel, concrete=True):
    """One label in a vocabulary shared by every non-anonymous actor."""

    _angee_base = AngeeHistoryMergeableUnscopedManager()

    sqid_prefix = "tag_"

    name = models.CharField(max_length=128)
    color = ColorField(max_length=32, blank=True, default="")
    attention = models.BooleanField(default=False, db_index=True)
    """Whether this tag marks a record for attention, beside the record's open decisions."""
    category = models.ForeignKey(
        "tags.TagCategory",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="tags",
    )
    """The one category this tag is filed under; ``None`` leaves it uncategorised."""

    objects = TagManager()

    class Meta:
        """Django model options for a tag."""

        abstract = True
        ordering = ("name", "sqid")
        rebac_resource_type = "tags/tag"

    def __str__(self) -> str:
        """Return the tag name for Django displays."""

        return self.name

    def save(self, *args: Any, **kwargs: Any) -> None:
        """Save the tag; filing it into a category must keep every record it tags within that maximum.

        A save that writes ``category`` re-judges the category's records, and a
        refusal rolls the save back.
        """

        update_fields = kwargs.get("update_fields")
        judged = (
            not self._state.adding
            and self.category_id is not None
            and (update_fields is None or bool({"category", "category_id"} & set(update_fields)))
        )
        with transaction.atomic(using=kwargs.get("using") or router.db_for_write(type(self), instance=self)):
            super().save(*args, **kwargs)
            if judged:
                assignment_model = apps.get_model(self._meta.app_label, "TagAssignment")
                assert issubclass(assignment_model, TagAssignment)
                assignment_model.objects.require_category_maximum(
                    models.Q(tag__category=self.category_id),
                    field="category",
                )

    def validate_merge(self, merged: Sequence[Self]) -> None:
        """Refuse merging tags filed elsewhere into a tag whose category sets a maximum.

        A merge moves the merged tags' assignments onto this tag, so their records
        gain a tag of this tag's category. File those tags into the category first,
        where the maximum is judged, then merge: a merge within one category only
        collapses tags, so no record's count rises.
        """

        super().validate_merge(merged)
        if self.category_id is None or all(record.category_id == self.category_id for record in merged):
            return
        category = apps.get_model(self._meta.app_label, "TagCategory")._base_manager.get(pk=self.category_id)
        assert isinstance(category, TagCategory)
        if category.max_per_record is not None:
            raise ValidationError(
                f"File the merged tags under {category} before merging them into {self}: "
                f"a record may hold at most {category.max_per_record} of its tags."
            )


class TagAssignmentQuerySet[AssignmentT: TagAssignment](AngeeHistoryQuerySet[AssignmentT]):
    """Chainable read scopes for the polymorphic tag edge."""

    def for_record(self, record: models.Model) -> Self:
        """Return the edges on one row, keyed by its canonical generic target."""

        return self.filter(**generic_target(record).lookups(self.model, "target"))

    def by_tag(self) -> Self:
        """Order edges as their tags list: the tag model's own ordering, through ``tag``."""

        tag_model = self.model.tag.field.related_model
        return self.order_by(*(f"tag__{name}" for name in (tag_model._meta.ordering or ())))


_TagAssignmentManagerBase = AngeeHistoryManager.from_queryset(TagAssignmentQuerySet)


class TagAssignmentManager[AssignmentT: TagAssignment](_TagAssignmentManagerBase[AssignmentT]):
    """Owns the polymorphic tag edge: target resolution, attach, detach, set, and the category maximum.

    Every read and write runs under the ambient actor. The target and each tag
    resolve through REBAC-scoped lookups, which fail fast on a row the actor
    cannot read, and the edge's own ``create`` and ``delete`` gates require the
    target authority its type's app declares, so a type no
    relation names is refused.
    """

    def resolve_target(self, target_type: str, target_id: str) -> GenericTarget | None:
        """Resolve the canonical edge target for a public target address.

        ``target_type`` is a REBAC resource type (e.g. ``parties/party``) and
        ``target_id`` the row's public id. Returns ``None`` when the type or row
        is unknown **or unreadable** — the lookup runs on the actor-scoped default
        manager. The returned :class:`rebac.GenericTarget` carries the
        ``content_type`` and ``object_id`` canonicalized to the target's topmost
        REBAC MTI ancestor (:func:`rebac.generic_target`): a ``parties/person``
        address and a ``parties/party`` address resolve to one ``parties/party``
        edge, so mixed-level addressing never splits the edge set.
        """

        model = model_for_resource_type(target_type)
        if model is None:
            return None
        instance = instance_from_public_id(model, target_id)
        if instance is None:
            return None
        return generic_target(instance)

    def for_target(self, target_type: str, target_id: str) -> TagAssignmentQuerySet[AssignmentT]:
        """Return the assignments on one target row, empty when it does not resolve."""

        target = self.resolve_target(target_type, target_id)
        if target is None:
            return self.none()
        return self.filter(**target.lookups(self.model, "target"))

    def attach(self, target_type: str, target_id: str, tag_ids: list[str]) -> list[Any]:
        """Attach each tag to the target row, idempotently per edge.

        Fails fast with :class:`ValueError` on an unresolvable target or tag (an
        unreadable row is indistinguishable from a missing one, by design); the
        edge's ``create`` gate enforces the target owner's declared tag authority.
        Under the target row's lock, a result over a category's maximum is refused
        (:meth:`require_category_maximum`) and nothing is attached.
        """

        target = self.resolve_target(target_type, target_id)
        if target is None:
            raise ValueError("tag target not found")
        tag_rows = [self._tag_for_id(tag_id) for tag_id in tag_ids]
        lookups = target.lookups(self.model, "target")
        with transaction.atomic():
            self._lock_target(target)
            assignments = [self.get_or_create(tag=tag_row, **lookups)[0] for tag_row in tag_rows]
            self.require_category_maximum(models.Q(**lookups), field="tags")
        return assignments

    def detach(self, target_type: str, target_id: str, tag_ids: list[str]) -> int:
        """Detach each tag from the target row; return the number of edges removed.

        Target and tags resolve as in :meth:`attach`; the edge's ``delete`` gate
        enforces the target owner's declared tag authority.
        """

        target = self.resolve_target(target_type, target_id)
        if target is None:
            raise ValueError("tag target not found")
        tag_pks = [self._tag_for_id(tag_id).pk for tag_id in tag_ids]
        deleted, _by_model = self.filter(tag_id__in=tag_pks, **target.lookups(self.model, "target")).delete()
        return deleted

    def set_for(self, record: models.Model, tag_ids: Iterable[str]) -> None:
        """Make ``record``'s tags exactly ``tag_ids``: attach the missing ones, detach the rest.

        Runs in the save of an owner's insert or update carrying the ``tags``
        input, under the same actor: the edge gates require its declared tag authority
        for each tag added or removed. Under the record's row lock, a result over a
        category's maximum is refused (:meth:`require_category_maximum`) and the
        record's tags stay as they were.
        """

        wanted = {self._tag_for_id(tag_id).pk for tag_id in tag_ids}
        target = generic_target(record)
        lookups = target.lookups(self.model, "target")
        with transaction.atomic():
            self._lock_target(target)
            current = set(self.filter(**lookups).values_list("tag_id", flat=True))
            self.filter(tag_id__in=current - wanted, **lookups).delete()
            for tag_pk in sorted(wanted - current):
                self.create(tag_id=tag_pk, **lookups)
            self.require_category_maximum(models.Q(**lookups), field="tags")

    def require_category_maximum(self, scope: models.Q, *, field: str = NON_FIELD_ERRORS) -> None:
        """Refuse when a record holds more tags of one category than the category's maximum.

        ``scope`` selects the edges to judge: one record's (its target lookups) or
        one category's (``tag__category``). Each record's edges in a category with a
        maximum count as named system work, so tags the actor cannot read count
        too. The first group over its maximum raises :class:`ValidationError` under
        ``field``. Callers run it inside the write's transaction, after the write,
        so a refusal rolls the write back: :meth:`attach` and :meth:`set_for` under
        the target row's lock, :meth:`TagCategory.save` and :meth:`Tag.save` when
        they write the maximum or the category. Queryset and bulk updates, like any
        write that bypasses those, are not judged.
        """

        over = (
            self.model.system_queryset()
            .filter(scope, tag__category__max_per_record__isnull=False)
            .values("tag__category", "content_type", "object_id")
            .annotate(held=models.Count("pk"))
            .filter(held__gt=models.F("tag__category__max_per_record"))
            .order_by("tag__category__position", "tag__category")
            .values_list("tag__category__name", "tag__category__max_per_record")
            .first()
        )
        if over is not None:
            name, maximum = over
            noun = "tag" if maximum == 1 else "tags"
            raise ValidationError({field: f"A record may hold at most {maximum} {noun} from {name}."})

    def _lock_target(self, target: GenericTarget) -> None:
        """Hold the target row's lock to the end of the transaction, serializing tag writes on it.

        The edge keys on the canonical model, so every address of the row takes the
        same lock; it is the ``lock_if_supported`` contract on any Django model.
        """

        model = target.content_type.model_class()
        assert model is not None
        list(model._base_manager.filter(pk=target.object_id).select_for_update(of=row_lock_paths(model)))

    def _tag_for_id(self, tag_id: str) -> Tag:
        """Return the actor-readable tag row for one public id, or fail fast."""

        tag_model = self.model.tag.field.related_model
        tag_row = instance_from_public_id(tag_model, str(tag_id))
        if tag_row is None:
            raise ValueError(f"tag {str(tag_id)!r} not found")
        return tag_row.canonical()


class TagAssignment(HistoryMixin, AuditMixin, RecordRefMixin, AngeeDataModel, concrete=True):
    """Polymorphic edge attaching one :class:`Tag` to a record of a taggable type.

    The ``target`` generic foreign key stores :func:`rebac.generic_target`. Each
    taggable type's app declares the ``target``-backed relation that authorizes
    the edge by its record (``permissions.zed``); :meth:`check` requires those
    relations and the models composing :class:`TaggedModel` to match.
    """

    history_revert_permission = "delete"
    merge_policy = MergePolicy.MOVE
    merge_identity = (("tag", "content_type", "object_id"),)
    sqid_prefix = "tga_"

    tag = models.ForeignKey(
        "tags.Tag",
        on_delete=models.CASCADE,
        related_name="assignments",
    )
    content_type = models.ForeignKey(ContentType, on_delete=models.CASCADE, related_name="+")
    object_id = models.PositiveBigIntegerField()
    target = GenericForeignKey("content_type", "object_id")

    objects = TagAssignmentManager()

    class Meta:
        """Django model options for a tag assignment."""

        abstract = True
        ordering = ("-created_at", "sqid")
        rebac_resource_type = "tags/tag_assignment"
        indexes = (models.Index(fields=("content_type", "object_id")),)
        constraints = (
            models.UniqueConstraint(
                fields=("tag", "content_type", "object_id"),
                name="%(app_label)s_assignment_tag_content_type_object_id",
            ),
        )

    def __str__(self) -> str:
        """Return a readable label for Django displays."""

        return f"{self.tag_id}->{self.content_type_id}:{self.object_id}"

    @classmethod
    def check(cls, **kwargs: Any) -> list[checks.CheckMessage]:
        """Require the taggable models and this edge's declared target types to match exactly.

        A taggable model composes :class:`TaggedModel` on its canonical model,
        the topmost REBAC-typed MTI ancestor its edges key on. Each needs a
        ``target``-backed relation on this edge's definition, or no actor can tag
        it; each declared target needs :class:`TaggedModel`, or its edges escape
        the record's ``tags`` field and its delete cascade. Read compiled schema
        declarations so the check also runs before database provisioning.
        """

        errors = super().check(**kwargs)
        owners: set[type[models.Model]] = set()
        for model in sorted(cls._meta.apps.get_models(), key=attrgetter("_meta.label")):
            if not issubclass(model, TaggedModel):
                continue
            owner = canonical_model(model)
            if owner is not None and issubclass(owner, TaggedModel):
                owners.add(owner)
                continue
            errors.append(
                checks.Error(
                    f"{model._meta.label} composes TaggedModel, but its rows are tagged as "
                    + (f"{owner._meta.label}, which does not." if owner else "no REBAC type."),
                    hint="Compose TaggedModel on the topmost REBAC-typed MTI ancestor.",
                    obj=model,
                    id="tags.E001",
                )
            )
        declared = set(cls.declared_target_models(definition_for=declared_rebac_definition).values())
        for owner in sorted(owners - declared, key=attrgetter("_meta.label")):
            errors.append(
                checks.Error(
                    f"{owner._meta.label} composes TaggedModel, but {model_resource_type(cls)} declares no "
                    f"relation for {model_resource_type(owner)}.",
                    hint="Declare the type's `target`-backed relation and its target_read and target_write "
                    "arms in the owning addon's permissions.extends.zed.",
                    obj=owner,
                    id="tags.E002",
                )
            )
        for target in sorted(declared - owners, key=attrgetter("_meta.label")):
            errors.append(
                checks.Error(
                    f"{model_resource_type(cls)} declares a relation for {model_resource_type(target)}, but "
                    f"{target._meta.label} does not compose TaggedModel.",
                    hint="Compose TaggedModel on the model, or remove the relation.",
                    obj=cls,
                    id="tags.E003",
                )
            )
        return errors


class TaggedModel(InputExtensionsMixin):
    """A model whose rows carry tags saved with the row.

    Declares the ``tag_assignments`` reverse accessor the tags read and the
    delete collector use, and consumes the ``tags`` value an owner's
    :func:`angee.tags.schema.tags_input_extensions` adds to its insert and set
    inputs: the row's write hands it here after the row is saved, in the same
    transaction. Compose it before the model base, on the topmost REBAC-typed
    MTI ancestor, and declare the type's relation on ``tags/tag_assignment``
    (:meth:`TagAssignment.check`).
    """

    tag_assignments = GenericRelation("tags.TagAssignment")

    class Meta:
        """Abstract taggable composition."""

        abstract = True

    def apply_input_extensions(self, *, tags: Iterable[str] | None = None, **values: Any) -> None:
        """Make this row's tags exactly ``tags`` when given, then delegate the remaining values."""

        if tags is not None:
            assignment_model = type(self)._meta.get_field("tag_assignments").related_model
            assert assignment_model is not None and issubclass(assignment_model, TagAssignment)
            assignment_model.objects.set_for(self, tags)
        super().apply_input_extensions(**values)


TagRole = role_anchor("tags/role", name="TagRole")
"""The ``tags/role`` anchor: its const ``admin`` arm resolves a platform admin as
an effective tags manager. See :func:`angee.base.models.roles.role_anchor`.
"""
