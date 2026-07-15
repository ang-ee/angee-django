"""Tags: a polymorphic shared labelling vocabulary.

A :class:`Tag` is one label in a vocabulary; a :class:`TagAssignment` is the
polymorphic edge attaching a tag to **any** row. The edge follows the
``storage.FileAttachment`` canon exactly — a ``content_type``/``object_id`` pair
with a :class:`~django.contrib.contenttypes.fields.GenericForeignKey` ``target`` —
so tags depend on nothing but ``angee.iam`` and reach every model without a FK
back to it. Consumers attach explicitly through
:meth:`TagAssignmentManager.attach` (create the edge against the concrete target)
exactly as storage consumers attach a file.

**Scope.** Base tags are shared vocabulary, readable by every authenticated actor
through a wildcard ``shared@auth/user:*`` reader tuple maintained by
:meth:`Tag.save`. Downstream addons may extend the row with their own scope field
and override :attr:`is_shared_scope`; the base model declares which stored fields
back that fact through :attr:`shared_scope_source_fields` so deferred loads do not
snapshot an unloaded value.

**Pitfalls.** Shared-tag visibility rides :meth:`Tag.save`: any write path that
skips ``save()`` — ``bulk_create``, ``queryset.update(...)``, raw
``loaddata`` — leaves the wildcard reader stale (an invisible shared tag or a
lingering everyone-grant); route scope changes through instance saves. And the
tuple write validates against the *loaded* REBAC schema, so creating a tag
requires ``rebac sync`` to have run first — the standard loop order
(``migrate`` → ``rebac sync`` → ``resources load``) already guarantees it.

**Party tags** compose this addon without any ``parties`` change: a party is
tagged by attaching to its ``Party`` row (the canon's explicit-attach path). The
ergonomic reverse accessor (``GenericRelation("tags.TagAssignment")`` on
``Party``) is a ``parties``-owned decision — adding it makes ``parties`` depend
on ``tags`` for every composing project, so it lands in ``parties`` (model +
``addon.toml`` dependency together) only when that dependency is wanted. Declare
that reverse relation on ``Party`` itself — the topmost REBAC-typed MTI ancestor
the canonical edge keys on (:func:`angee.base.refs.canonical_record_target`), never
on a ``Person``/``Organization`` child — so the delete collector filters at the same
content type the write used (the placement invariant in :mod:`angee.base.refs`).
"""

from __future__ import annotations

from typing import Any, ClassVar

from django.contrib.contenttypes.fields import GenericForeignKey
from django.contrib.contenttypes.models import ContentType
from django.db import models, transaction
from rebac import (
    RelationshipTuple,
    SubjectRef,
    delete_relationships,
    system_context,
    to_object_ref,
    write_relationships,
)
from rebac.resources import model_for_resource_type
from rebac.types import RelationshipFilter

from angee.base.mixins import ArchiveMixin, ArchiveQuerySet, AuditMixin, SqidMixin
from angee.base.models import (
    AngeeDataModel,
    AngeeManager,
    AngeeModel,
    AngeeQuerySet,
    instance_from_public_id,
    role_anchor,
)
from angee.base.refs import CanonicalRecordTarget, RecordRefMixin, canonical_record_target

SHARED_READER_RELATION = "shared"
"""The wildcard-subject relation that opens a shared tag to everyone."""

_EVERYONE = SubjectRef.of("auth/user", "*")
"""The ``auth/user:*`` wildcard subject — every authenticated actor at once."""

_NEVER_LOADED = object()
"""Sentinel for "this instance was not loaded from the DB" in the save-time diff."""


class TagQuerySet(ArchiveQuerySet[Any], AngeeQuerySet[Any]):
    """Archive read scopes layered over the REBAC-scoped tag queryset."""


TagManager = AngeeManager.from_queryset(TagQuerySet)


class Tag(ArchiveMixin, AngeeDataModel):
    """One label in a shared vocabulary.

    :meth:`save` keeps the wildcard reader tuple in step with
    :attr:`is_shared_scope` so the REBAC read scope stays truthful without a
    queryset override.
    """

    runtime = True
    sqid_prefix = "tag_"
    shared_scope_source_fields: ClassVar[tuple[str, ...]] = ()
    """Stored fields from which :attr:`is_shared_scope` is computed."""

    name = models.CharField(max_length=128)
    color = models.CharField(max_length=32, blank=True, default="")

    objects = TagManager()

    class Meta:
        """Django model options for a tag."""

        abstract = True
        ordering = ("name", "sqid")
        rebac_resource_type = "tags/tag"
        rebac_id_attr = "sqid"

    def __str__(self) -> str:
        """Return the tag name for Django displays."""

        return self.name

    @property
    def is_shared_scope(self) -> bool:
        """Return whether this row should carry the shared wildcard reader."""

        return True

    @classmethod
    def from_db(cls, db: Any, field_names: Any, values: Any) -> "Tag":
        """Load a row, snapshotting shared scope when its source fields are loaded.

        Tag owns the original-value snapshot the reconcile compares against (the
        canonical Django "track the loaded value" shape). If any declared source
        field is deferred, the snapshot is skipped so :meth:`save` fail-safes into
        the idempotent re-sync rather than evaluating a missing row fact.
        """

        instance = super().from_db(db, field_names, values)
        source_attnames = cls._shared_scope_source_attnames()
        if all(attname in field_names for attname in source_attnames):
            instance._loaded_is_shared_scope = instance.is_shared_scope
        else:
            instance._loaded_is_shared_scope = _NEVER_LOADED
        return instance

    def save(self, *args: Any, **kwargs: Any) -> None:
        """Persist the row and reconcile its shared-reader wildcard tuple atomically.

        A shared tag carries a ``shared@auth/user:*`` tuple that opens it to every
        actor. Row and tuple commit or roll back together — a shared tag must
        never land without its reader, nor a scoped transition leave a stale
        grant. The reconcile runs only when the row is new, no complete snapshot
        exists, or the shared/scoped fact changes; an instance with no snapshot
        fail-safes into the idempotent re-sync.
        """

        adding = self._state.adding
        loaded_is_shared_scope = getattr(self, "_loaded_is_shared_scope", _NEVER_LOADED)
        with transaction.atomic():
            super().save(*args, **kwargs)
            if (
                adding
                or loaded_is_shared_scope is _NEVER_LOADED
                or loaded_is_shared_scope != self.is_shared_scope
            ):
                self._sync_shared_reader()
            self._loaded_is_shared_scope = self.is_shared_scope

    def _sync_shared_reader(self) -> None:
        """Grant or revoke the ``shared@auth/user:*`` reader for this tag's scope."""

        resource = to_object_ref(self)
        if self.is_shared_scope:
            write_relationships(
                [
                    RelationshipTuple(
                        resource=resource,
                        relation=SHARED_READER_RELATION,
                        subject=_EVERYONE,
                    )
                ]
            )
        else:
            delete_relationships(
                RelationshipFilter(
                    resource_type=resource.resource_type,
                    resource_id=resource.resource_id,
                    relation=SHARED_READER_RELATION,
                    subject_type=_EVERYONE.subject_type,
                    subject_id=_EVERYONE.subject_id,
                )
            )

    @classmethod
    def _shared_scope_source_attnames(cls) -> tuple[str, ...]:
        """Return attnames for declared shared-scope source fields."""

        attnames: list[str] = []
        for field_name in cls.shared_scope_source_fields:
            field = cls._meta.get_field(field_name)
            attnames.append(getattr(field, "attname", field.name))
        return tuple(attnames)


class TagAssignmentManager(AngeeManager):
    """Owns the polymorphic tag edge: target resolution, attach, and detach.

    The write protocol (the ``storage.FileManager.draft`` shape): the target and
    every tag resolve **under the ambient actor** — the REBAC-scoped lookups fail
    fast on a row the actor cannot read, so nobody tags or untags what they cannot
    see — and only the edge insert/delete itself runs under ``system_context``,
    because ``tags/tag_assignment`` declares no ``create`` permission (rows enter
    through gated call sites, the ``FileAttachment`` precedent) and the pre-insert
    check has no row id to gate on.
    """

    def resolve_target(self, target_type: str, target_id: str) -> CanonicalRecordTarget | None:
        """Resolve the canonical edge target for a public target address.

        ``target_type`` is a REBAC resource type (e.g. ``parties/party``) and
        ``target_id`` the row's public id. Returns ``None`` when the type or row
        is unknown **or unreadable** — the lookup runs on the actor-scoped default
        manager. The returned :class:`~angee.base.refs.CanonicalRecordTarget` carries
        the ``content_type`` and ``object_id`` canonicalized to the target's topmost
        REBAC MTI ancestor (:func:`angee.base.refs.canonical_record_target`): a
        ``parties/person`` address and a ``parties/party`` address resolve to one
        ``parties/party`` edge, so mixed-level addressing never splits the edge set.
        """

        model = model_for_resource_type(target_type)
        if model is None:
            return None
        instance = instance_from_public_id(model, target_id)
        if instance is None:
            return None
        return canonical_record_target(instance)

    def for_target(self, target_type: str, target_id: str) -> models.QuerySet[Any]:
        """Return the assignments on one target row, empty when it does not resolve."""

        target = self.resolve_target(target_type, target_id)
        if target is None:
            return self.none()
        return self.filter(content_type=target.content_type, object_id=target.object_id)

    def attach(self, target_type: str, target_id: str, tag_ids: list[str]) -> list[Any]:
        """Attach each tag to the target row, idempotently per edge.

        Fails fast with :class:`ValueError` on an unresolvable target or tag (an
        unreadable row is indistinguishable from a missing one, by design). Only
        the ``get_or_create`` runs elevated; ``created_by`` still stamps from the
        ambient actor, which elevation preserves.
        """

        target = self.resolve_target(target_type, target_id)
        if target is None:
            raise ValueError("tag target not found")
        tag_rows = [self._tag_for_id(tag_id) for tag_id in tag_ids]
        with system_context(reason="tags.assignment.attach"):
            return [
                self.get_or_create(
                    tag=tag_row, content_type=target.content_type, object_id=target.object_id
                )[0]
                for tag_row in tag_rows
            ]

    def detach(self, target_type: str, target_id: str, tag_ids: list[str]) -> int:
        """Detach each tag from the target row; return the number of edges removed.

        Same protocol as :meth:`attach`: target and tags resolve under the actor,
        only the delete elevates.
        """

        target = self.resolve_target(target_type, target_id)
        if target is None:
            raise ValueError("tag target not found")
        tag_pks = [self._tag_for_id(tag_id).pk for tag_id in tag_ids]
        with system_context(reason="tags.assignment.detach"):
            deleted, _by_model = self.filter(
                content_type=target.content_type, object_id=target.object_id, tag_id__in=tag_pks
            ).delete()
        return deleted

    def _tag_for_id(self, tag_id: str) -> Any:
        """Return the actor-readable tag row for one public id, or fail fast."""

        tag_model = self.model._meta.get_field("tag").related_model
        tag_row = instance_from_public_id(tag_model, str(tag_id))
        if tag_row is None:
            raise ValueError(f"tag {str(tag_id)!r} not found")
        return tag_row


class TagAssignment(SqidMixin, AuditMixin, RecordRefMixin, AngeeModel):
    """Polymorphic edge attaching one :class:`Tag` to any model row.

    The exact ``storage.FileAttachment`` canon: a ``content_type``/``object_id``
    pair with a :class:`GenericForeignKey` ``target``. Consumers attach explicitly
    through :meth:`TagAssignmentManager.attach` — the party-tag path targets a
    ``parties.Party`` row. Access rides entirely on the ``tag`` parent (see
    ``permissions.zed``), the same way a file attachment rides its file: the
    polymorphic target is not a single REBAC type, so no arrow can cover it.
    """

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
        rebac_id_attr = "sqid"
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


TagRole = role_anchor("tags/role", name="TagRole")
"""The ``tags/role`` anchor: its const ``admin`` arm resolves a platform admin as
an effective tags manager. See :func:`angee.base.models.role_anchor`.
"""
