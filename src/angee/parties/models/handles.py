"""Native handles model owners for the contacts domain."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any, cast

from django.apps import apps
from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models, transaction
from phonenumbers import (
    NumberParseException,
    PhoneNumberFormat,
    format_number,
    is_possible_number,
    is_valid_number,
    parse,
)
from rebac import database_context, system_context
from rebac.database import write_alias

from angee.base.fields.state import StateField
from angee.base.models.audit import AuditMixin
from angee.base.models.base import AngeeDataModel
from angee.base.models.merge import MergeableMixin
from angee.base.permissions import instance_actor
from angee.parties.connections import ConnectionHandle
from angee.parties.managers import (
    HandleManager,
)
from angee.provenance.contrib.angee.history import HistoryMixin
from angee.provenance.contrib.angee.managers import AngeeHistoryMergeableUnscopedManager
from angee.tags.models import TaggedModel


class Handle(MergeableMixin, HistoryMixin, TaggedModel, AuditMixin, AngeeDataModel, concrete=True):
    """A reachable address or handle of a party on one platform.

    Keyed on ``(platform, value)`` and, when present, ``(platform, external_id)``
    — those unique constraints are the ingestion-dedup keys that make re-sync
    idempotent. ``party`` is the resolved identity the Handle manager
    materialises; it is null until a handle is linked, so a handle synced for an
    unknown sender is still a valid row. A handle carries tags of its own, apart
    from its party's (a consumer marks a company's accounts-payable address).
    """

    _angee_base = AngeeHistoryMergeableUnscopedManager()

    rebac_grantable = {"reader": "write"}
    retains_merged = True

    class Platform(models.TextChoices):
        """The kind of channel a handle reaches a party through."""

        EMAIL = "email", "Email"
        PHONE = "phone", "Phone"
        DISCORD = "discord", "Discord"
        IMESSAGE = "imessage", "iMessage"
        MATRIX = "matrix", "Matrix"
        SIGNAL = "signal", "Signal"
        SLACK = "slack", "Slack"
        TELEGRAM = "telegram", "Telegram"
        WHATSAPP = "whatsapp", "WhatsApp"
        YOUTUBE = "youtube", "YouTube"
        FACEBOOK = "facebook", "Facebook"
        INSTAGRAM = "instagram", "Instagram"
        LINKEDIN = "linkedin", "LinkedIn"
        TWITTER = "twitter", "Twitter"
        OTHER = "other", "Other"

        @classmethod
        def for_value(cls, value: str) -> Handle.Platform:
            """Classify a raw handle value through the one platform heuristic."""

            return cast(Handle.Platform, cls.EMAIL if "@" in (value or "") else cls.OTHER)

    sqid_prefix = "hdl_"
    platform = StateField(choices_enum=Platform, default=Platform.EMAIL)
    value = models.CharField(max_length=512)
    normalized_value = models.CharField(max_length=512, db_index=True, editable=False)
    external_id = models.CharField(max_length=512, blank=True, default="")
    display_name = models.CharField(max_length=4096, blank=True, default="")
    label = models.CharField(max_length=64, blank=True, default="")
    is_preferred = models.BooleanField(default=False)
    controller = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="controlled_handles",
        db_index=True,
    )
    """The user who controls this address — sends, posts, or syncs as it.

    The **control** fact, distinct from the **identity** fact (``party`` — who the
    address reaches, resolved from member statements) and from access, which
    belongs to the handle's creator. A shared team inbox or a service account may
    carry a ``controller`` without resolving to that user's party, and an
    ex-address may reach a person's party without anyone controlling it.
    """

    is_verified = models.BooleanField(default=False)
    metadata = models.JSONField(blank=True, default=dict)
    party = models.ForeignKey(
        "parties.Party",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="handles",
    )
    party_link_confirmed = models.BooleanField(default=False, editable=False)
    """Whether the winning link materialised into ``party`` is human-confirmed."""

    avatar = models.ForeignKey(
        "storage.File",
        null=True,
        blank=True,
        editable=False,
        on_delete=models.SET_NULL,
        related_name="handle_avatars",
    )
    """The avatar the handle's own network shows for it (a messenger profile picture).

    Written only by :meth:`set_avatar`, onto storage's image drive every signed-in user reads.
    """

    objects = HandleManager()

    class Meta:
        """Django model options for the handle source model."""

        abstract = True
        ordering = ("platform", "value", "sqid")
        rebac_resource_type = "parties/handle"
        constraints = (
            models.UniqueConstraint(
                fields=("platform", "value"),
                name="uq_handle_platform_value",
            ),
            models.UniqueConstraint(
                fields=("platform", "external_id"),
                condition=~models.Q(external_id=""),
                name="uq_handle_platform_external_id",
            ),
        )

    def __str__(self) -> str:
        """Return the handle value for Django displays."""

        return self.value

    @classmethod
    def identified_connection(cls, parsed: ConnectionHandle) -> bool:
        """Whether a connection names its account by a real identifier, not by a display name only.

        An email address, a phone number or a platform id identifies one account.
        An adapter may override that inference with its typed confidence.
        """

        if parsed.confidence is not None:
            return parsed.confidence >= 1.0
        return bool(parsed.external_id) or parsed.platform in {cls.Platform.EMAIL, cls.Platform.PHONE}

    @classmethod
    def normalize_value(cls, platform: str, value: str) -> str:
        """Return the persisted comparison value for one platform/value pair.

        Every platform strips surrounding whitespace and lowercases. Email keeps
        that rule and additionally collapses dots and plus-tags in Gmail local
        parts for both ``gmail.com`` and ``googlemail.com`` domains. Phone and
        WhatsApp values parse without an assumed region and format as E.164; values
        require a leading country code and must be possible and valid. Anything
        unparseable, invalid, or region-unknown falls back to its digits so
        punctuation still does not fork the same contact point. iMessage carries
        either kind of address, so it routes by value shape — the email rule when
        the value contains ``@``, the phone rule otherwise.
        """

        normalized = (value or "").strip().lower()
        if platform in (cls.Platform.EMAIL, cls.Platform.IMESSAGE) and "@" in normalized:
            local, _, domain = normalized.rpartition("@")
            if domain in ("gmail.com", "googlemail.com"):
                local = local.split("+", 1)[0].replace(".", "")
            return f"{local}@{domain}"
        if platform in (cls.Platform.PHONE, cls.Platform.WHATSAPP, cls.Platform.IMESSAGE):
            try:
                number = parse(normalized, None)
            except NumberParseException:
                number = None
            if number is not None and is_possible_number(number) and is_valid_number(number):
                return format_number(number, PhoneNumberFormat.E164)
            return "".join(character for character in normalized if character.isdigit())
        return normalized

    @staticmethod
    def normalize_display_name(value: str) -> str:
        """Return the comparison key used by cross-platform display-name pooling."""

        return " ".join((value or "").split()).casefold()

    def save(self, *args: Any, **kwargs: Any) -> None:
        """Persist the handle while keeping ``normalized_value`` in lockstep."""

        update_fields = kwargs.get("update_fields")
        normalization_fields = {"platform", "value", "normalized_value"}
        if self._state.adding or update_fields is None or normalization_fields.intersection(update_fields):
            self.normalized_value = self.normalize_value(self.platform, self.value)
            if update_fields is not None and "normalized_value" not in update_fields:
                kwargs["update_fields"] = [*update_fields, "normalized_value"]
        super().save(*args, **kwargs)

    def validate_merge(self, merged: Sequence[Any]) -> None:
        """A contact alias keeps its platform and the directory identity lock order."""

        super().validate_merge(merged)
        if any(row.platform != self.platform for row in merged):
            raise ValidationError("Only handles on the same platform can be merged.")
        apps.get_model("parties", "Party").objects.db_manager(self._state.db).lock_identity_rows(
            party_ids=(),
            handle_ids=(self.pk, *(row.pk for row in merged)),
        )

    def merge_values(self, merged: Sequence[Any]) -> dict[str, Any]:
        """Project missing contact display/control values without writing a fold."""
        values: dict[str, Any] = {}
        for row in merged:
            for name in ("controller", "label", "display_name", "is_verified"):
                field = self._meta.get_field(name)
                assert isinstance(field, models.Field)
                attname = field.attname
                if not values.get(attname, getattr(self, attname)) and getattr(row, attname):
                    values[attname] = getattr(row, attname)
        return values

    def merge_fields(self, merged: Sequence[Any], **options: Any) -> None:
        """Fill missing contact display/control facts; retain the survivor's choices."""
        super().merge_fields(merged, **options)
        values = self.merge_values(merged)
        for name, value in values.items():
            setattr(self, name, value)
        if values:
            self.save(using=self._state.db, update_fields=(*values, "updated_at"))
        for row in merged:
            if self.avatar_id is None and row.avatar_id is not None:
                self.set_avatar(row.avatar)

    def retire_merged(self, merged: Any) -> None:
        """Recompute identity projections after shared reference moves, then redirect."""

        merged.resolve()
        self.resolve()
        # Resolution wrote the party on its own locked row; the redirect's history
        # snapshot must carry that party, not this instance's stale one.
        merged.refresh_from_db(using=self._state.db, fields=("party", "party_link_confirmed"))
        super().retire_merged(merged)

    def resolve(self) -> None:
        """Materialize the applied, eligible cross-party winner and both counters."""
        handle_id = self.pk
        using = write_alias(type(self), using=self._state.db, instance=self)
        owner = apps.get_model("parties", "Party").objects.db_manager(using)
        with database_context(using), transaction.atomic(using=using), system_context(reason="parties.handle.resolve"):
            handles, parties = owner.lock_identity_rows(party_ids=(), handle_ids=(handle_id,))
            handle = handles[handle_id]
            candidates = list(
                owner.handle_statements()
                .applied()
                .filter(
                    handle=handle,
                    subject__is_trashed=False,
                )
                .select_related("subject", "link__stream")
            )
            winner = min(candidates, key=lambda row: row.sort_key(), default=None)
            previous = handle.party_id
            resolved = winner.subject_id if winner else None
            confirmed = bool(winner and winner.is_confirmed)
            if handle.party_id != resolved or handle.party_link_confirmed != confirmed:
                handle.party_id, handle.party_link_confirmed = resolved, confirmed
                handle.save(using=using, update_fields=("party", "party_link_confirmed", "updated_at"))
            for pk in sorted({value for value in (previous, resolved) if value is not None}):
                parties[pk].recount_handles()
            handle._party_links_resolved()
            self.party_id, self.party_link_confirmed = handle.party_id, handle.party_link_confirmed

    def set_avatar(self, avatar: Any | None) -> bool:
        """Set the avatar ``handle``'s own network shows for it; return whether it changed.

        The one write path of :attr:`Handle.avatar`: a source reports the avatar
        (:meth:`HandleManager.fill_avatar`), never writes the column itself. ``avatar`` is a stored
        image (``File.objects.ingest_image``), or ``None`` when the network shows none.
        """

        avatar_id = avatar.pk if avatar is not None else None
        owner = type(self).objects.db_manager(self._state.db, hints={"instance": self})
        with (
            database_context(owner.db),
            system_context(reason="parties.handle.set_avatar"),
            transaction.atomic(using=owner.db),
        ):
            locked = owner.filter(pk=self.pk).lock_if_supported().first()
            if locked is None or locked.avatar_id == avatar_id:
                return False
            locked.avatar_id = avatar_id
            locked.save(update_fields=["avatar", "updated_at"])
        self.avatar_id = avatar_id
        return True

    def _party_links_resolved(self) -> None:
        """Run addon contributions after this handle's party is fully resolved.

        The parties owner invokes this hook inside the same database transaction as
        its derived ``party`` and counter writes.  Base parties has no additional
        work; addons may retain a durable consequence of the completed transition.
        """

        return None

    @property
    def resolved_confidence(self) -> float | None:
        """Confidence of the subject-authorized applied party assertion."""
        if self.party_id is None:
            return None
        if "_resolved_confidence" in self.__dict__:
            return cast(float | None, self.__dict__["_resolved_confidence"])
        row = (
            apps.get_model("parties", "Party")
            .objects.db_manager(self._state.db)
            .handle_statements()
            .for_reader(instance_actor(self))
            .applied()
            .filter(subject_id=self.party_id, handle_id=self.pk)
            .first()
        )
        return row.confidence if row is not None else None
