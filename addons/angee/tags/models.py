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
:class:`angee.base.mixins.ConditionalSharedReaderMixin`. Downstream addons may
extend the row with their own scope field and override :attr:`is_shared_scope`;
the canonical reconciler reloads persisted facts before changing the wildcard.

**Pitfalls.** Eligibility fields cannot be changed through queryset or bulk
writes, and conditional-reader rows cannot be bulk-created; route those changes
through the native owner so row and tuple stay atomic. The tuple write validates
against the *loaded* REBAC schema, so creating a tag
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

from angee.base.identity import instance_from_public_id
from angee.base.mixins import (
    ArchiveMixin,
    ArchiveQuerySet,
    AuditMixin,
    ConditionalSharedReaderMixin,
    ConditionalSharedReaderQuerySet,
    SqidMixin,
)
from angee.base.models import (
    AngeeDataModel,
    AngeeManager,
    AngeeModel,
    AngeeQuerySet,
    role_anchor,
)
from angee.base.refs import CanonicalRecordTarget, RecordRefMixin, canonical_record_target
from django.contrib.contenttypes.fields import GenericForeignKey
from django.contrib.contenttypes.models import ContentType
from django.db import models, transaction
from rebac import (
    system_context,
)
from rebac.resources import model_for_resource_type


class TagQuerySet(
    ConditionalSharedReaderQuerySet[Any],
    ArchiveQuerySet[Any],
    AngeeQuerySet[Any],
):
    """Archive read scopes layered over the REBAC-scoped tag queryset."""


TagManager = AngeeManager.from_queryset(TagQuerySet)


class Tag(ConditionalSharedReaderMixin, ArchiveMixin, AngeeDataModel):
    """One label in a shared vocabulary.

    :meth:`save` keeps the wildcard reader tuple in step with
    :attr:`is_shared_scope` so the REBAC read scope stays truthful without a
    queryset override.
    """

    runtime = True
    sqid_prefix = "tag_"
    shared_scope_source_fields: ClassVar[tuple[str, ...]] = ()
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
    def shared_reader_eligible(self) -> bool:
        """Delegate to the established downstream shared-scope hook."""

        return self.is_shared_scope

    @property
    def is_shared_scope(self) -> bool:
        """Base native tags are shared; downstream scope donors may narrow them."""

        return True

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
