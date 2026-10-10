"""Tags: a polymorphic shared labelling vocabulary.

A :class:`Tag` is one label in a vocabulary; a :class:`TagAssignment` is the
polymorphic edge attaching a tag to a record of a taggable type: a
``content_type``/``object_id`` pair with a
:class:`~django.contrib.contenttypes.fields.GenericForeignKey` ``target``, stored
at :func:`rebac.generic_target`. Tags depend on nothing but ``angee.iam`` and
reach every taggable model without a FK back to it.

**Scope.** Tags are reference vocabulary readable by every non-anonymous actor
through the native ``authenticated`` permission; tag administrators curate it.
A tag assignment is authorized by the record it tags (``permissions.zed``):
adding or removing a tag needs write on the record, and an assignment is visible
to whoever reads both the tag and the record.

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

from collections.abc import Iterable
from operator import attrgetter
from typing import Any, cast

from django.contrib.contenttypes.fields import GenericForeignKey, GenericRelation
from django.contrib.contenttypes.models import ContentType
from django.core import checks
from django.db import models
from rebac import GenericTarget, generic_target
from rebac.field_backing import canonical_model
from rebac.resources import model_for_resource_type, model_resource_type

from angee.base.fields import ColorField
from angee.base.identity import instance_from_public_id
from angee.base.merge import MergeableMixin
from angee.base.mixins import (
    ArchiveMixin,
    ArchiveQuerySet,
    AuditMixin,
)
from angee.base.models import (
    AngeeDataModel,
    AngeeManager,
    AngeeQuerySet,
    role_anchor,
)
from angee.base.refs import MergePolicy, RecordRefMixin


class TagQuerySet(
    ArchiveQuerySet[Any],
    AngeeQuerySet[Any],
):
    """Archive read scopes layered over the REBAC-scoped tag queryset."""


TagManager = AngeeManager.from_queryset(TagQuerySet)


class Tag(MergeableMixin, ArchiveMixin, AngeeDataModel):
    """One label in a vocabulary shared by every non-anonymous actor."""

    runtime = True
    sqid_prefix = "tag_"

    name = models.CharField(max_length=128)
    color = ColorField(max_length=32, blank=True, default="")

    objects = TagManager()

    class Meta:
        """Django model options for a tag."""

        abstract = True
        ordering = ("name", "sqid")
        rebac_resource_type = "tags/tag"

    def __str__(self) -> str:
        """Return the tag name for Django displays."""

        return self.name


class TagAssignmentQuerySet(AngeeQuerySet[Any]):
    """Chainable read scopes for the polymorphic tag edge."""

    def for_record(self, record: models.Model) -> TagAssignmentQuerySet:
        """Return the edges on one row, keyed by its canonical generic target."""

        return cast(TagAssignmentQuerySet, self.filter(**generic_target(record).lookups(self.model, "target")))

    def by_tag(self) -> TagAssignmentQuerySet:
        """Order edges as their tags list: the tag model's own ordering, through ``tag``."""

        tag_model = self.model._meta.get_field("tag").related_model
        return cast(TagAssignmentQuerySet, self.order_by(*(f"tag__{name}" for name in tag_model._meta.ordering)))


class TagAssignmentManager(AngeeManager.from_queryset(TagAssignmentQuerySet)):  # type: ignore[misc]
    """Owns the polymorphic tag edge: target resolution, attach, detach, and set.

    Every read and write runs under the ambient actor. The target and each tag
    resolve through REBAC-scoped lookups, which fail fast on a row the actor
    cannot read, and the edge's own ``create`` and ``delete`` gates require write
    on the target through the relation its type's app declares, so a type no
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

    def for_target(self, target_type: str, target_id: str) -> models.QuerySet[Any]:
        """Return the assignments on one target row, empty when it does not resolve."""

        target = self.resolve_target(target_type, target_id)
        if target is None:
            return self.none()
        return self.filter(**target.lookups(self.model, "target"))

    def attach(self, target_type: str, target_id: str, tag_ids: list[str]) -> list[Any]:
        """Attach each tag to the target row, idempotently per edge.

        Fails fast with :class:`ValueError` on an unresolvable target or tag (an
        unreadable row is indistinguishable from a missing one, by design); the
        edge's ``create`` gate refuses an actor without write on the target.
        """

        target = self.resolve_target(target_type, target_id)
        if target is None:
            raise ValueError("tag target not found")
        tag_rows = [self._tag_for_id(tag_id) for tag_id in tag_ids]
        lookups = target.lookups(self.model, "target")
        return [self.get_or_create(tag=tag_row, **lookups)[0] for tag_row in tag_rows]

    def detach(self, target_type: str, target_id: str, tag_ids: list[str]) -> int:
        """Detach each tag from the target row; return the number of edges removed.

        Target and tags resolve as in :meth:`attach`; the edge's ``delete`` gate
        refuses an actor without write on the target.
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
        input, under the same actor: the edge gates require write on the record
        for each tag added or removed.
        """

        wanted = {self._tag_for_id(tag_id).pk for tag_id in tag_ids}
        lookups = generic_target(record).lookups(self.model, "target")
        current = set(self.filter(**lookups).values_list("tag_id", flat=True))
        self.filter(tag_id__in=current - wanted, **lookups).delete()
        for tag_pk in sorted(wanted - current):
            self.create(tag_id=tag_pk, **lookups)

    def _tag_for_id(self, tag_id: str) -> Any:
        """Return the actor-readable tag row for one public id, or fail fast."""

        tag_model = self.model._meta.get_field("tag").related_model
        tag_row = instance_from_public_id(tag_model, str(tag_id))
        if tag_row is None:
            raise ValueError(f"tag {str(tag_id)!r} not found")
        return tag_row


class TagAssignment(AuditMixin, RecordRefMixin, AngeeDataModel):
    """Polymorphic edge attaching one :class:`Tag` to a record of a taggable type.

    The ``target`` generic foreign key stores :func:`rebac.generic_target`. Each
    taggable type's app declares the ``target``-backed relation that authorizes
    the edge by its record (``permissions.zed``); :meth:`check` requires those
    relations and the models composing :class:`TaggedModel` to match.
    """

    merge_policy = MergePolicy.MOVE
    merge_identity = (("tag", "content_type", "object_id"),)
    runtime = True
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
        the record's ``tags`` field and its delete cascade.
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
            errors.append(checks.Error(
                f"{model._meta.label} composes TaggedModel, but its rows are tagged as "
                + (f"{owner._meta.label}, which does not." if owner else "no REBAC type."),
                hint="Compose TaggedModel on the topmost REBAC-typed MTI ancestor.",
                obj=model,
                id="tags.E001",
            ))
        declared = set(cls.declared_target_models().values())
        for owner in sorted(owners - declared, key=attrgetter("_meta.label")):
            errors.append(checks.Error(
                f"{owner._meta.label} composes TaggedModel, but {model_resource_type(cls)} declares no "
                f"relation for {model_resource_type(owner)}.",
                hint="Declare the type's `target`-backed relation and its target_read and target_write "
                "arms in the owning addon's permissions.extends.zed.",
                obj=owner,
                id="tags.E002",
            ))
        for target in sorted(declared - owners, key=attrgetter("_meta.label")):
            errors.append(checks.Error(
                f"{model_resource_type(cls)} declares a relation for {model_resource_type(target)}, but "
                f"{target._meta.label} does not compose TaggedModel.",
                hint="Compose TaggedModel on the model, or remove the relation.",
                obj=cls,
                id="tags.E003",
            ))
        return errors


class TaggedModel(models.Model):
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
            type(self)._meta.get_field("tag_assignments").related_model.objects.set_for(self, tags)
        super().apply_input_extensions(**values)


TagRole = role_anchor("tags/role", name="TagRole")
"""The ``tags/role`` anchor: its const ``admin`` arm resolves a platform admin as
an effective tags manager. See :func:`angee.base.models.role_anchor`.
"""
