"""Group ownership, live roster audiences, and members' notification preferences.

``Group`` composes transferable ownership and messaging's audience contract.
``Membership`` owns each party's role, confirmation and notification preference;
only confirmed, non-dismissed rows grant roster access. ``ThreadSpace`` binds
threads to groups; ``ChannelSpace`` and ``VaultSpace`` bind channels and
knowledge vaults to a team on their own rows. REBAC reads these facts live,
including public visibility from the group column.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence
from typing import Any, Self

from django.apps import apps
from django.core.exceptions import ValidationError
from django.db import models, transaction
from django.utils.text import slugify
from rebac import PermissionDenied, current_actor

from angee.base.fields import StateField
from angee.base.mixins import AuditMixin, HierarchyMixin, OwnerMixin
from angee.base.models import AngeeDataModel
from angee.messaging.models import AudienceMember, NotificationPolicy, ThreadAudienceMixin
from angee.parties.mixins import ScoredLinkMixin
from angee.spaces.managers import GroupManager, MembershipManager


class Group(ThreadAudienceMixin, HierarchyMixin, OwnerMixin, AngeeDataModel):
    """A shared group with one canonical roster and an unscoped parent tree."""

    runtime = True
    sqid_prefix = "grp_"

    class GroupVisibility(models.TextChoices):
        """Whether membership is required to read the group and its threads."""

        PUBLIC = "public", "Public"
        PRIVATE = "private", "Private"

    name = models.CharField(max_length=200)
    slug = models.SlugField(blank=True, unique=True)
    description = models.TextField(blank=True, default="")
    visibility = StateField(
        choices_enum=GroupVisibility,
        default=GroupVisibility.PRIVATE,
    )

    objects = GroupManager()

    class Meta(HierarchyMixin.Meta):
        """Django options carrying the hierarchy path index and REBAC identity."""

        abstract = True
        ordering = ("name", "sqid")
        rebac_resource_type = "spaces/group"

    def __str__(self) -> str:
        """Return the group name for Django displays."""

        return self.name

    def thread_audience(self) -> Iterable[AudienceMember]:
        """Read participating roster parties; messaging owns delivery and read checks."""

        membership_model = apps.get_model("spaces", "Membership")
        rows = (
            membership_model.system_queryset()
            .confirmed()
            .filter(
                group_id=self.pk,
                role__in=(
                    membership_model.MembershipRole.OWNER,
                    membership_model.MembershipRole.MODERATOR,
                    membership_model.MembershipRole.MEMBER,
                ),
            )
            .only("party_id", "notification_policy", "subtype_keys")
            .order_by("pk")
        )
        for row in rows.iterator():
            yield AudienceMember(
                party_id=row.party_id,
                notification_policy=NotificationPolicy(row.notification_policy),
                subtype_keys=tuple(row.subtype_keys),
            )

    def save(self, *args: Any, **kwargs: Any) -> None:
        """Persist the group with a unique slug."""

        with transaction.atomic():
            if not self.slug:
                self.slug = self._available_slug()
                update_fields = kwargs.get("update_fields")
                if update_fields is not None:
                    kwargs["update_fields"] = tuple(dict.fromkeys((*update_fields, "slug")))
            super().save(*args, **kwargs)

    def _available_slug(self) -> str:
        """Return the first name-derived slug unused in the shared Group table."""

        slug_field = self._meta.get_field("slug")
        max_length = slug_field.max_length or 50
        base = slugify(self.name)[:max_length] or "group"
        owner_model = slug_field.model
        candidates = owner_model.system_queryset(lock=("self",))
        if self.pk is not None:
            candidates = candidates.exclude(pk=self.pk)

        candidate = base
        suffix = 1
        while candidates.filter(slug=candidate).exists():
            suffix += 1
            ending = f"-{suffix}"
            candidate = f"{base[: max_length - len(ending)]}{ending}"
        return candidate


class Membership(ScoredLinkMixin, AuditMixin, AngeeDataModel):
    """One party's role-bearing roster row in a shared group.

    Confirmation resolves live through the canonical ``Person.user`` identity
    link. External parties without platform users remain roster rows but grant no
    REBAC access.
    """

    runtime = True
    sqid_prefix = "mbr_"

    class MembershipRole(models.TextChoices):
        """The access role a confirmed roster row grants on its group."""

        OWNER = "owner", "Owner"
        MODERATOR = "moderator", "Moderator"
        MEMBER = "member", "Member"
        VIEWER = "viewer", "Viewer"

    group = models.ForeignKey(
        "spaces.Group",
        on_delete=models.CASCADE,
        related_name="memberships",
    )
    party = models.ForeignKey(
        "parties.Party",
        on_delete=models.CASCADE,
        related_name="space_memberships",
    )
    role = StateField(choices_enum=MembershipRole, default=MembershipRole.MEMBER)
    notification_policy = StateField(
        choices_enum=NotificationPolicy, default=NotificationPolicy.INBOX, db_index=False,
    )
    subtype_keys = models.JSONField(blank=True, default=list)
    objects = MembershipManager()

    @transaction.atomic
    def dismiss(self) -> None:
        """End follows whose team read disappears with this roster seat."""

        super().dismiss()
        apps.get_model("messaging", "ThreadFollower").objects.end_unreadable_for_party(self.party)

    @transaction.atomic
    def delete(self, *args: Any, **kwargs: Any) -> tuple[int, dict[str, int]]:
        """Apply the same read cleanup when removing the seat altogether."""

        party = self.party
        result = super().delete(*args, **kwargs)
        apps.get_model("messaging", "ThreadFollower").objects.end_unreadable_for_party(party)
        return result

    class Meta:
        """Django options for the canonical group roster edge."""

        abstract = True
        ordering = ("group", "role", "sqid")
        rebac_resource_type = "spaces/membership"
        constraints = (
            models.UniqueConstraint(
                fields=("group", "party"),
                name="uq_%(app_label)s_membership_group_party",
            ),
        )

    def __str__(self) -> str:
        """Return a readable membership description for Django displays."""

        return f"{self.party_id}∈{self.group_id} ({self.role})"

    def set_notifications(self, policy: NotificationPolicy, subtype_keys: Sequence[str]) -> Self:
        """Set this holder's preference after checking its live identity under a row lock."""

        actor = self.actor() or current_actor()
        if actor is None:
            raise PermissionDenied("An acting user is required to set notification preferences.")
        with transaction.atomic():
            locked = type(self).system_queryset(lock=("self",)).get(pk=self.pk).with_actor(actor)
            if not locked.has_access("set_notifications"):
                raise PermissionDenied("Only the membership holder can set notification preferences.")
            locked.notification_policy = policy
            locked.subtype_keys = list(subtype_keys)
            locked.full_clean(validate_unique=False, validate_constraints=False)
            if any(not isinstance(key, str) or not key for key in locked.subtype_keys):
                raise ValidationError({"subtype_keys": "Select nonempty message subtype keys."})
            keys = set(locked.subtype_keys)
            if len(keys) != len(locked.subtype_keys):
                raise ValidationError({"subtype_keys": "Select each message subtype only once."})
            subtype_model = apps.get_model("messaging", "MessageSubtype")
            declared = set(subtype_model.builtin_options())
            declared.update(
                subtype_model.system_queryset().filter(key__in=keys).values_list("key", flat=True),
            )
            if keys - declared:
                raise ValidationError({"subtype_keys": "Select declared message subtype keys."})
            locked.sudo(reason="spaces.membership.set_notifications").save(
                update_fields=["notification_policy", "subtype_keys", "updated_at"],
            )
            self.refresh_from_db()
        return self


class ThreadSpace(models.Model):
    """Group audience contributed onto ``messaging.Thread`` as a same-row field."""

    extends = "messaging.Thread"

    groups = models.ManyToManyField(
        "spaces.Group",
        blank=True,
        related_name="threads",
    )

    class Meta:
        """Abstract same-row extension composed into ``messaging.Thread``."""

        abstract = True


class ChannelSpace(models.Model):
    """Bind a messaging channel to a team through its integration parent."""

    extends = "messaging.Channel"
    hasura_readable_fields = ("team",)
    hasura_filterable_fields = hasura_readable_fields
    hasura_insertable_fields = hasura_readable_fields
    hasura_updatable_fields = hasura_readable_fields

    team = models.ForeignKey(
        "spaces.Group",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="channels",
    )

    class Meta:
        """Same-row contribution owned by the addon that knows channels and teams."""

        abstract = True


class VaultSpace(models.Model):
    """Bind a knowledge vault's access to the roster of an optional team.

    The column is the one record of the binding: the roster reads the vault
    through the group's ``post`` and writes it through ``write``; rebinding the
    team is gated by the vault's ``share``, because it hands the vault to another
    roster. Group viewers and public readers gain nothing through it.
    """

    extends = "knowledge.Vault"
    hasura_readable_fields = ("team",)
    hasura_filterable_fields = hasura_readable_fields
    hasura_insertable_fields = hasura_readable_fields
    hasura_updatable_fields = hasura_readable_fields

    team = models.ForeignKey(
        "spaces.Group",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="vaults",
    )

    class Meta:
        """Same-row contribution owned by the addon that knows vaults and teams."""

        abstract = True
