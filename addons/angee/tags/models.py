"""Tags: a polymorphic, shared-or-company-scoped labelling vocabulary.

A :class:`Tag` is one label in a vocabulary; a :class:`TagAssignment` is the
polymorphic edge attaching a tag to **any** row. The edge follows the
``storage.FileAttachment`` canon exactly — a ``content_type``/``object_id`` pair
with a :class:`~django.contrib.contenttypes.fields.GenericForeignKey` ``target`` —
so tags depend on nothing but ``angee.iam`` and reach every model without a FK
back to it. Consumers attach explicitly through
:meth:`TagAssignmentManager.attach` (create the edge against the concrete target)
exactly as storage consumers attach a file.

**Scope.** ``Tag.company`` is **nullable**: a null-company tag is *shared
vocabulary* readable by every authenticated actor; a company-scoped tag is
isolated to that company (``permissions.zed``: ``read = shared + company->member``).
Scope is delivered through REBAC, not a queryset override — a scoped tag is
field-backed by ``company`` (invisible cross-company), and a shared tag is opened
to everyone through a wildcard ``shared@auth/user:*`` reader tuple maintained by
:meth:`Tag.save` (the wildcard-subject relation ``storage/drive`` also allows for
its everyone-grants; the save-time upkeep is this addon's own, because *shared* is
a row fact here, not an optional grant). This is why ``Tag`` does **not** compose
``CompanyScopedMixin`` — that mixin forces a non-null company and would forbid the
shared vocabulary; ``Tag`` owns its own nullable ``company`` FK instead.

**Pitfalls.** Shared-tag visibility rides :meth:`Tag.save`: any write path that
skips ``save()`` — ``bulk_create``, ``queryset.update(company=...)``, raw
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
``addon.toml`` dependency together) only when that dependency is wanted.
"""

from __future__ import annotations

from typing import Any

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
from angee.base.refs import RecordRefMixin

SHARED_READER_RELATION = "shared"
"""The wildcard-subject relation that opens a shared (null-company) tag to everyone."""

_EVERYONE = SubjectRef.of("auth/user", "*")
"""The ``auth/user:*`` wildcard subject — every authenticated actor at once."""

_NEVER_LOADED = object()
"""Sentinel for "this instance was not loaded from the DB" in the save-time diff."""


class TagQuerySet(ArchiveQuerySet[Any], AngeeQuerySet[Any]):
    """Archive read scopes layered over the REBAC-scoped tag queryset."""


TagManager = AngeeManager.from_queryset(TagQuerySet)


class Tag(ArchiveMixin, AngeeDataModel):
    """One label in a shared-or-company-scoped vocabulary.

    ``company`` is nullable by design (see the module docstring): ``None`` is
    *shared* vocabulary every actor reads, a set company scopes the tag to that
    company. :meth:`save` keeps the wildcard reader tuple in step with
    ``company`` so the REBAC read scope stays truthful without a queryset
    override.
    """

    runtime = True
    sqid_prefix = "tag_"

    name = models.CharField(max_length=128)
    color = models.CharField(max_length=32, blank=True, default="")
    company = models.ForeignKey(
        "iam.Company",
        on_delete=models.PROTECT,
        related_name="+",
        null=True,
        blank=True,
    )
    """The owning company, or ``None`` for shared vocabulary (see module docstring)."""

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

    @classmethod
    def from_db(cls, db: Any, field_names: Any, values: Any) -> "Tag":
        """Load a row, snapshotting its ``company_id`` so :meth:`save` spots a rescope.

        Tag owns the original-value snapshot the reconcile compares against (the
        canonical Django "track the loaded value" shape), rather than reading a
        framework-internal probe. ``company`` deferred out of the load leaves no
        snapshot, so :meth:`save` fail-safes into the idempotent re-sync.
        """

        instance = super().from_db(db, field_names, values)
        instance._loaded_company_id = (
            instance.company_id if "company_id" in field_names else _NEVER_LOADED
        )
        return instance

    def save(self, *args: Any, **kwargs: Any) -> None:
        """Persist the row and reconcile its shared-reader wildcard tuple atomically.

        A shared (null-company) tag carries a ``shared@auth/user:*`` tuple that
        opens it to every actor; a company-scoped tag carries none (it reads
        through the field-backed ``company`` arm instead). Row and tuple commit
        or roll back together — a shared tag must never land without its reader,
        nor a rescope leave a stale grant. The reconcile runs only when the row
        is new or ``company`` actually changed (against the :meth:`from_db`
        snapshot), so unrelated edits do not re-issue grants; an instance with no
        snapshot fail-safes into the idempotent re-sync.
        """

        adding = self._state.adding
        loaded_company_id = getattr(self, "_loaded_company_id", _NEVER_LOADED)
        with transaction.atomic():
            super().save(*args, **kwargs)
            if adding or loaded_company_id is _NEVER_LOADED or loaded_company_id != self.company_id:
                self._sync_shared_reader()

    def _sync_shared_reader(self) -> None:
        """Grant or revoke the ``shared@auth/user:*`` reader for this tag's scope."""

        resource = to_object_ref(self)
        if self.company_id is None:
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

    def resolve_target(self, target_type: str, target_id: str) -> tuple[Any, Any] | None:
        """Resolve ``(content_type, instance)`` for a public target address.

        ``target_type`` is a REBAC resource type (e.g. ``parties/party``) and
        ``target_id`` the row's public id. Returns ``None`` when the type or row
        is unknown **or unreadable** — the lookup runs on the actor-scoped default
        manager. Address a polymorphic target at one consistent type: an MTI child
        (``parties/person``) and its parent (``parties/party``) are distinct
        content types, so mixed-level addressing splits the edge set.
        """

        model = model_for_resource_type(target_type)
        if model is None:
            return None
        instance = instance_from_public_id(model, target_id)
        if instance is None:
            return None
        return ContentType.objects.get_for_model(model), instance

    def for_target(self, target_type: str, target_id: str) -> models.QuerySet[Any]:
        """Return the assignments on one target row, empty when it does not resolve."""

        resolved = self.resolve_target(target_type, target_id)
        if resolved is None:
            return self.none()
        content_type, instance = resolved
        return self.filter(content_type=content_type, object_id=instance.pk)

    def attach(self, target_type: str, target_id: str, tag_ids: list[str]) -> list[Any]:
        """Attach each tag to the target row, idempotently per edge.

        Fails fast with :class:`ValueError` on an unresolvable target or tag (an
        unreadable row is indistinguishable from a missing one, by design). Only
        the ``get_or_create`` runs elevated; ``created_by`` still stamps from the
        ambient actor, which elevation preserves.
        """

        resolved = self.resolve_target(target_type, target_id)
        if resolved is None:
            raise ValueError("tag target not found")
        content_type, instance = resolved
        tag_rows = [self._tag_for_id(tag_id) for tag_id in tag_ids]
        with system_context(reason="tags.assignment.attach"):
            return [
                self.get_or_create(tag=tag_row, content_type=content_type, object_id=instance.pk)[0]
                for tag_row in tag_rows
            ]

    def detach(self, target_type: str, target_id: str, tag_ids: list[str]) -> int:
        """Detach each tag from the target row; return the number of edges removed.

        Same protocol as :meth:`attach`: target and tags resolve under the actor,
        only the delete elevates.
        """

        resolved = self.resolve_target(target_type, target_id)
        if resolved is None:
            raise ValueError("tag target not found")
        content_type, instance = resolved
        tag_pks = [self._tag_for_id(tag_id).pk for tag_id in tag_ids]
        with system_context(reason="tags.assignment.detach"):
            deleted, _by_model = self.filter(
                content_type=content_type, object_id=instance.pk, tag_id__in=tag_pks
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
