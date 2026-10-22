"""Source models for the parties addon.

Parties are the people and organisations a project keeps track of. A party is
reached through one or more :class:`Handle` rows (an email address, a phone
number, a social handle) — the same handle that messaging uses as a participant,
so this addon is the contacts foundation the messaging addon builds on. The link
between a party and a handle is a confidence-bearing member statement
so a sync can record an uncertain match as a weak candidate instead of guessing.

``Party`` is a multi-table-inheritance parent; the concrete kind is the child
model (:class:`Person`, :class:`Organization`), not a column — a person carries
name parts and a link to its :class:`~angee.iam.models.User`, an organisation
carries its legal name and domain.

Parties are organised two ways, both human-owned facts: :class:`Circle` is a
private, overlapping grouping (a party may belong to many circles; circles nest
as a :class:`~angee.models.HierarchyMixin` tree), and :class:`Relationship`
is a typed, directed party↔party edge whose vocabulary
(:class:`RelationshipKind`) is catalogue data seeded from the XFN / vCard
``RELATED`` values — never a schema fact.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import replace
from typing import Any, ClassVar, cast

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
from rebac import PermissionDenied, actor_context
from rebac.mixins import RebacModelBase

from angee.actors import instance_actor
from angee.fields import ColorField, StateField
from angee.identity import public_id_of
from angee.impl import ImplClassField
from angee.integrate.models import Bridge, IntegrationCreateMode
from angee.lineage import concrete_child, concrete_child_models
from angee.models import (
    AngeeDataModel,
    AngeeManager,
    Aspect,
    AuditMixin,
    HierarchyMixin,
    HistoryMixin,
    MergeableMixin,
    ModelHistory,
    OwnerMixin,
    PairAspect,
    SourceKind,
    StatementRecords,
    StatementsMixin,
    StatementSubjectManager,
    TrashMixin,
)
from angee.parties.backends import DirectoryBackend
from angee.parties.fields import CountryCodeField
from angee.parties.managers import (
    CircleManager,
    HandleManager,
    PartyManager,
    PartyStatementQuerySet,
)
from angee.parties.mixins import MembershipDecisionMixin, PartyAspectMixin, PartyStatementMixin
from angee.refs import RetainedGenericRelation
from angee.tags.models import TaggedModel


class Party(MergeableMixin, TrashMixin, StatementsMixin, HistoryMixin, TaggedModel, OwnerMixin, AngeeDataModel):
    """A person or organisation the project tracks.

    The parent owns the common contact identity — the public id, ownership, the
    display name, avatar, notes and tags. Ownership is the transferable ``owner``
    (:class:`~angee.models.OwnerMixin`), which an insert defaults to the
    creator; ``created_by`` stays attribution. RecordLink owns directory payloads.
    The concrete kind (and its kind-specific fields) lives on the :class:`Person` /
    :class:`Organization` child row, which inherits the owner; a child's tags key
    on its party.
    """

    runtime = True
    rebac_grantable = {"reader": "write"}

    sqid_prefix = "pty_"
    history = ModelHistory(inherit=True, excluded_fields=("handle_count",))
    REVIEW_CONFIDENCE: ClassVar[float] = 0.5

    def handle_candidates(self, candidates: Sequence[Any]) -> Sequence[Any]:
        return [row for row in candidates if row.confidence >= self.REVIEW_CONFIDENCE]

    def apply_handle(self, *, member: Mapping[str, Any], **kwargs: Any) -> None:
        apps.get_model("parties", "Handle").objects.db_manager(self._state.db).resolve(member["handle"])

    record_links = RetainedGenericRelation(
        "integrate.RecordLink", content_type_field="target_content_type", object_id_field="target_object_id"
    )
    statements = StatementRecords(
        link="integrate.RecordLink",
        aspects={
            "identity": Aspect(
                fields=("display_name",),
                precedence=cast(
                    tuple[SourceKind, ...],
                    (SourceKind.PERSON, SourceKind.SYSTEM_OF_RECORD, SourceKind.DIRECTORY, SourceKind.IMPORT),
                ),
            ),
            "contact": Aspect(
                fields=("notes", "avatar"),
                precedence=cast(tuple[SourceKind, ...], (SourceKind.PERSON, SourceKind.DIRECTORY)),
            ),
            "handle": Aspect(
                member={"handle": models.ForeignKey("parties.Handle", on_delete=models.PROTECT)},
                precedence=cast(
                    tuple[SourceKind, ...],
                    (
                        SourceKind.PERSON,
                        SourceKind.SYSTEM_OF_RECORD,
                        SourceKind.DIRECTORY,
                        SourceKind.IMPORT,
                        SourceKind.RULE,
                    ),
                ),
                candidates=handle_candidates,
                apply=apply_handle,
            ),
        },
        sqid_prefix="pst_",
        bases=(PartyStatementMixin,),
        historical_queryset=PartyStatementQuerySet,
    )
    same_as = PairAspect(link="integrate.RecordLink")
    MERGED_PERMISSION = "write"
    retains_merged = True

    display_name = models.TextField()
    notes = models.TextField(blank=True, default="")
    avatar = models.ForeignKey(
        "storage.File",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="party_avatars",
    )
    """The party's own avatar (a directory card's ``PHOTO``), on storage's image drive every signed-in user reads."""
    handle_count = models.PositiveIntegerField(default=0, db_index=True)
    introduced_by = models.ForeignKey(
        "self",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="introductions",
    )
    """The party that introduced this one — acquaintance provenance, not a typed edge."""

    first_met_note = models.TextField(blank=True, default="")
    """Free-text "how I know them" note (vCard has no property for this)."""

    objects = PartyManager()

    _merge_scalar_fields: ClassVar[tuple[str, ...]] = (
        "display_name",
        "notes",
        "first_met_note",
    )
    _person_merge_scalar_fields: ClassVar[tuple[str, ...]] = (
        "name_prefix",
        "given_name",
        "additional_name",
        "family_name",
        "name_suffix",
        "nickname",
        "birthday",
    )

    class Meta:
        """Django model options for the party source model."""

        abstract = True
        ordering = ("-updated_at", "display_name", "sqid")
        verbose_name_plural = "parties"
        rebac_resource_type = "parties/party"
        constraints = (
            models.CheckConstraint(
                condition=~models.Q(merged_into=models.F("id")),
                name="ck_party_not_merged_into_self",
            ),
        )

    def lock_subject(self, members: Mapping[str, Any] | None = None) -> Any:
        """Lock identity Handles before the statement owner's core Party lock."""

        owner = apps.get_model("parties", "Party").objects.db_manager(hints={"instance": self})
        members = members or {}
        handle = members.get("handle")
        handle_ids = (
            (handle.pk if isinstance(handle, models.Model) else handle,)
            if handle is not None
            else owner.handle_statements().filter(subject_id=self.pk).values_list("handle_id", flat=True)
        )
        owner.lock_identity_rows(party_ids=(self.pk,), handle_ids=handle_ids)
        return super().lock_subject(members)

    def lock_for_merge(self) -> None:
        """Lock this party's Handles before the shared merge owner locks Parties."""

        owner = apps.get_model("parties", "Party").objects.db_manager(hints={"instance": self})
        handle_ids = owner.handle_statements().filter(subject_id=self.pk).values_list("handle_id", flat=True)
        owner.lock_identity_rows(party_ids=(self.pk,), handle_ids=handle_ids)

    def __str__(self) -> str:
        """Return the party's display name for Django displays."""

        return self.display_name

    PLACEHOLDER_NAME = "Unknown"
    """The display name imports write when a source carries no name at all."""

    @property
    def has_real_name(self) -> bool:
        """Whether ``display_name`` is a human-given name.

        The import placeholder and letterless names (a phone number or other
        handle spelling promoted into the name slot) do not count. When links tie,
        a handle resolves to a party that passes
        (:meth:`~angee.parties.managers.HandleManager.resolve`).
        """

        name = (self.display_name or "").strip()
        if not name or name == self.PLACEHOLDER_NAME:
            return False
        return any(character.isalpha() for character in name)

    @property
    def concrete_kind(self) -> RelationshipKind.PartyKind | None:
        """Return this party's concrete kind (``"person"`` / ``"organization"``), or ``None``.

        A structural fact read straight from the multi-table-inheritance child rows
        that share this party's primary key — not a stored column (the child model
        *is* the kind). Read through the base manager: it is a system integrity fact,
        never actor-scoped user data.
        """

        for child_model in concrete_child_models(apps.get_model("parties", "Party")):
            if isinstance(self, child_model) or concrete_child(self, child_model) is not None:
                for kind in (RelationshipKind.PartyKind.ORGANIZATION, RelationshipKind.PartyKind.PERSON):
                    if RelationshipKind.PartyKind(kind).model() is child_model:
                        return cast(RelationshipKind.PartyKind, kind)
        return None

    def apply_merge_field_overrides(
        self,
        source: Party,
        field_overrides: Any,
    ) -> None:
        """Apply the allow-listed scalar overrides selected for a merge survivor.

        Common human fields live on ``Party``. Person-only name and birthday
        fields are accepted only when both endpoints materialize a ``Person``
        child; the child owns coercion and persistence for those columns.
        """

        if field_overrides is None:
            return
        if not isinstance(field_overrides, Mapping):
            raise ValidationError({"field_overrides": "Expected an object of field values."})

        both_people = self.concrete_kind == "person" and source.concrete_kind == "person"
        allowed = set(self._merge_scalar_fields)
        if both_people:
            allowed.update(self._person_merge_scalar_fields)
        unknown = set(field_overrides) - allowed
        if unknown:
            names = ", ".join(sorted(str(name) for name in unknown))
            raise ValidationError({"field_overrides": f"Unsupported merge field(s): {names}."})

        self._apply_merge_scalar_values(field_overrides, self._merge_scalar_fields)
        if both_people and any(name in field_overrides for name in self._person_merge_scalar_fields):
            person_model = apps.get_model("parties", "Person")
            person = person_model.objects.filter(pk=self.pk).first()
            if person is None or not person.has_access("write"):
                raise PermissionDenied("write access to the surviving person is required")
            person = person.with_actor(instance_actor(self))
            person._apply_merge_scalar_values(field_overrides, self._person_merge_scalar_fields)

    def _apply_merge_scalar_values(
        self,
        field_overrides: Mapping[Any, Any],
        field_names: tuple[str, ...],
    ) -> None:
        """Coerce and save this row's selected merge fields through Django fields."""

        dirty: list[str] = []
        for name in field_names:
            if name not in field_overrides:
                continue
            field = cast(models.Field[Any, Any], self._meta.get_field(name))
            value = field.clean(field_overrides[name], self)
            if getattr(self, name) != value:
                setattr(self, name, value)
                dirty.append(name)
        if dirty:
            self.save(update_fields=[*dirty, "updated_at"])

    decision_methods = ("apply_identity",)

    def apply_identity(
        self,
        *,
        expected_facts_hash: str = "",
        proposed: Mapping[str, Any] | None = None,
        choices: Mapping[str, Any] | None = None,
    ) -> tuple[str, dict[str, Any]]:
        """Apply a proposed identity through the directory's conflict-checking owner."""
        if proposed is None or choices is None or not expected_facts_hash:
            raise ValidationError("An identity application needs its proposed values, choices and facts hash.")
        return type(self).objects.apply_identity(
            party_id=public_id_of(self),
            expected_facts_hash=expected_facts_hash,
            proposed=proposed,
            choices=choices,
            actor=instance_actor(self),
        )

    def identity_differs(self, current: Mapping[str, Any], proposed: Mapping[str, Any]) -> bool:
        """Compare proposed native identity values with a retained read projection."""

        if proposed["name"] and proposed["name"] != current["name"]:
            return True
        addresses = apps.get_model("parties", "Address").objects
        address = proposed["address"]
        if any(address.get(field) for field in addresses.components):
            proposed_key = addresses.identity_key(address)
            if all(addresses.identity_key(row) != proposed_key for row in current["addresses"]):
                return True
        link_id = proposed["handle"]["statement_id"]
        statement_model = self.statements.model
        confirmed = (statement_model.StatementRank.PREFERRED, statement_model.StatementSnak.VALUE)
        return bool(
            link_id
            and any(
                row["id"] == link_id and (row["rank"], row["snak"]) != confirmed
                for row in current["handles"]
            )
        )

    def identity_values(
        self, addresses: list[Any], links: list[Any], *, include_evidence: bool = False
    ) -> dict[str, Any]:
        """Project the exact native identity facts used by expected-state checks."""

        return {
            "name": self.display_name,
            "addresses": [
                {
                    "id": str(row.sqid),
                    "label": row.label,
                    "is_primary": row.is_primary,
                    **{field: getattr(row, field) for field in AddressManager.components},
                }
                for row in addresses
            ],
            "handles": [
                {
                    "id": str(link.sqid),
                    "handle_id": str(link.handle.sqid),
                    "platform": str(link.handle.platform),
                    "value": link.handle.value,
                    "confidence": link.confidence,
                    "asserted_by": link.asserted_by,
                    "rank": link.rank,
                    "snak": link.snak,
                    "status": link.status,
                    "applied": link.applied,
                    **({"evidence": link.evidence} if include_evidence else {}),
                }
                for link in links
            ],
        }

    def apply_pair(self, *, aspect: str, other: Any, statement: Any | None) -> None:
        """Identity survivorship selects a pending decision; merge chooses the survivor."""

        if aspect != "same_as":
            super().apply_pair(aspect=aspect, other=other, statement=statement)

    def validate_merge(self, merged: Sequence[Any]) -> None:
        super().validate_merge(merged)
        for row in merged:
            if self.same_as.kept_separate(self, row):
                raise ValidationError("These parties have been marked to stay separate.")

    def merge_fields(self, merged: Sequence[Any], *, field_overrides: Any = None, **options: Any) -> None:
        super().merge_fields(merged, **options)
        for row in merged:
            self.apply_merge_field_overrides(row, field_overrides)
            self.same_as.for_pair(self, row).with_actor(instance_actor(self)).confirm(aspect="same_as", values={})


class Person(models.Model, metaclass=RebacModelBase):
    """A human party — carries name parts and an optional platform-user link."""

    runtime = True
    extends: ClassVar[str | None] = "parties.Party"

    statements = StatementRecords(
        link="integrate.RecordLink",
        aspects={
            "name": Aspect(
                fields=("name_prefix", "given_name", "additional_name", "family_name", "name_suffix", "nickname"),
                precedence=cast(
                    tuple[SourceKind, ...],
                    (SourceKind.PERSON, SourceKind.SYSTEM_OF_RECORD, SourceKind.DIRECTORY, SourceKind.IMPORT),
                ),
            ),
            "dates": Aspect(
                fields=("birthday", "anniversary"),
                precedence=cast(
                    tuple[SourceKind, ...],
                    (SourceKind.PERSON, SourceKind.SYSTEM_OF_RECORD, SourceKind.DIRECTORY, SourceKind.IMPORT),
                ),
            ),
        },
        sqid_prefix="pns_",
    )

    name_prefix = models.TextField(blank=True, default="")
    given_name = models.TextField(blank=True, default="")
    additional_name = models.TextField(blank=True, default="")
    family_name = models.TextField(blank=True, default="")
    name_suffix = models.TextField(blank=True, default="")
    nickname = models.TextField(blank=True, default="")
    birthday = models.DateField(null=True, blank=True)
    anniversary = models.DateField(null=True, blank=True)
    user = models.OneToOneField(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="person",
    )

    class Meta:
        """Django model options for the person child model."""

        abstract = True
        ordering = ("-updated_at", "display_name", "sqid")
        rebac_resource_type = "parties/person"


class Organization(models.Model, metaclass=RebacModelBase):
    """An organisation party — carries its legal name and primary domain."""

    runtime = True
    extends: ClassVar[str | None] = "parties.Party"

    statements = StatementRecords(
        link="integrate.RecordLink",
        aspects={
            "legal_identity": Aspect(
                fields=("legal_name", "domain"),
                precedence=cast(
                    tuple[SourceKind, ...], (SourceKind.PERSON, SourceKind.SYSTEM_OF_RECORD, SourceKind.DIRECTORY)
                ),
            )
        },
        sqid_prefix="ogs_",
    )

    legal_name = models.TextField(blank=True, default="")
    domain = models.CharField(max_length=255, blank=True, default="", db_index=True)

    class Meta:
        """Django model options for the organization child model."""

        abstract = True
        ordering = ("-updated_at", "display_name", "sqid")
        rebac_resource_type = "parties/organization"


class Handle(MergeableMixin, HistoryMixin, TaggedModel, AuditMixin, AngeeDataModel):
    """A reachable address or handle of a party on one platform.

    Keyed on ``(platform, value)`` and, when present, ``(platform, external_id)``
    — those unique constraints are the ingestion-dedup keys that make re-sync
    idempotent. ``party`` is the resolved identity the Handle manager
    materialises; it is null until a handle is linked, so a handle synced for an
    unknown sender is still a valid row. A handle carries tags of its own, apart
    from its party's (a consumer marks a company's accounts-payable address).
    """

    runtime = True
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

    Written only by :meth:`HandleManager.set_avatar`, onto storage's image drive every signed-in user reads.
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
            party_ids=(), handle_ids=(self.pk, *(row.pk for row in merged)),
        )

    def merge_fields(self, merged: Sequence[Any], **options: Any) -> None:
        """Fill missing contact display/control facts; retain the survivor's choices."""

        super().merge_fields(merged, **options)
        for row in merged:
            filled = []
            for name in ("controller", "label", "display_name", "is_verified"):
                field = self._meta.get_field(name)
                assert isinstance(field, models.Field)
                attname = field.attname
                if not getattr(self, attname) and getattr(row, attname):
                    setattr(self, attname, getattr(row, attname))
                    filled.append(name)
            if filled:
                self.save(using=self._state.db, update_fields=(*filled, "updated_at"))
            if self.avatar_id is None and row.avatar_id is not None:
                type(self).objects.db_manager(self._state.db).set_avatar(self, row.avatar)

    def retire_merged(self, merged: Any) -> None:
        """Recompute identity projections after shared reference moves, then redirect."""

        owner = type(self).objects.db_manager(self._state.db)
        owner.resolve(merged)
        owner.resolve(self)
        # Resolution wrote the party on its own locked row; the redirect's history
        # snapshot must carry that party, not this instance's stale one.
        merged.refresh_from_db(using=self._state.db, fields=("party", "party_link_confirmed"))
        super().retire_merged(merged)

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
            .objects.handle_statements()
            .for_reader(instance_actor(self))
            .applied()
            .filter(subject_id=self.party_id, handle_id=self.pk)
            .first()
        )
        return row.confidence if row is not None else None


class AddressManager[AddressT: Address](StatementSubjectManager[AddressT]):
    """Own normalized, conflict-safe attachment of canonical party addresses."""

    components = ("po_box", "extended", "street", "city", "region", "postal_code", "country")

    def as_stored(self, address: Any) -> Any:
        """Return a parsed address as its fields store it: a country name becomes its ISO code.

        A card's components keep their whitespace (a street may span lines), and a
        value its field refuses (an unresolvable country) stays as written, for
        ingest to refuse. Both sides of a directory sync compare this form, so a
        card's "USA" reads as the stored "US".
        """

        values = {name: getattr(address, name) for name in self.components}
        return replace(address, **self._through_fields(values, lenient=True))

    def _normalize_components(self, values: Mapping[str, Any]) -> dict[str, str]:
        """Collapse the whitespace of free-text components, then store them through their fields."""

        return self._through_fields({name: " ".join(str(values.get(name) or "").split()) for name in self.components})

    def _through_fields(self, values: Mapping[str, Any], *, lenient: bool = False) -> dict[str, Any]:
        """Pass each component through its own field's ``to_python``.

        A refused value raises, or with ``lenient`` stays as written.
        """

        stored: dict[str, Any] = {}
        for name in self.components:
            try:
                field = self.model._meta.get_field(name)
                assert isinstance(field, models.Field)
                stored[name] = field.to_python(values[name])
            except ValidationError:
                if not lenient:
                    raise
                stored[name] = values[name]
        return stored

    def identity_key(self, values: Mapping[str, Any]) -> tuple[str, ...]:
        """Return the field-normalized, case-insensitive identity of an address."""

        normalized = self._normalize_components(values)
        return tuple(normalized[field].casefold() for field in self.components)

    def lock_party(self, party_id: Any) -> None:
        """Serialize address writes for one party, including its first address."""

        party_model = self.model.party.field.related_model
        party_model.objects.sudo(
            reason="parties.address.lock_party",
        ).locked_get(pk=party_id)

    def demote_primaries(self, address: Address) -> None:
        """Authorize and demote other primaries inside the selecting save's transaction."""

        self.lock_party(address.party_id)
        previous = list(
            self.sudo(reason="parties.address.primary_integrity")
            .lock_if_supported()
            .filter(party_id=address.party_id, is_primary=True)
            .exclude(pk=address.pk)
        )
        actor, unscoped = address.effective_actor(strict=True)
        for row in previous:
            if not unscoped and not row.with_actor(actor).has_access("write"):
                raise PermissionDenied(
                    "Changing the primary address requires write access to the current primary address."
                )
        self.sudo(reason="parties.address.demote_primaries").filter(
            pk__in=[row.pk for row in previous],
        ).update(is_primary=False)

    def attach_exact(
        self,
        *,
        party: models.Model,
        values: Mapping[str, Any],
        actor: Any,
        label: str = "Primary",
        is_primary: bool = True,
        conflict: str = "raise",
    ) -> tuple[str, AddressT | None]:
        if conflict not in {"raise", "retain", "append"}:
            raise ValueError("Address conflict policy must be 'raise', 'retain', or 'append'.")
        normalized = self._normalize_components(values)
        if not any(normalized.values()):
            return "missing", None
        key = self.identity_key(normalized)
        with transaction.atomic(), actor_context(actor):
            self.lock_party(party.pk)
            existing = list(
                self.sudo(reason="parties.address.attach_exact").lock_if_supported().filter(party=party).order_by("pk")
            )
            for row in existing:
                row_key = self.identity_key({field: getattr(row, field) for field in self.components})
                if row_key == key:
                    if not row.with_actor(actor).has_access("read"):
                        raise PermissionDenied("Denied: cannot read the matching party address.")
                    return "matched", row.with_actor(actor)
            if existing:
                if conflict == "retain":
                    return "conflict", None
                if conflict == "raise":
                    raise ValidationError({"address": "A different address already exists for this party."})
                is_primary = False
            row = self.model(
                party=party,
                label=" ".join(label.split()).strip()[:64],
                is_primary=is_primary,
                created_by_id=getattr(actor, "pk", None),
                **normalized,
            )
            row = self.with_actor(actor).insert(row)
            return "created", row.with_actor(actor)

    def replace_primary_exact(
        self,
        *,
        party: models.Model,
        values: Mapping[str, Any],
        actor: Any,
        expected_id: Any | None,
        label: str = "Primary",
    ) -> tuple[str, AddressT]:
        """Replace the frozen primary address, or create it when none existed."""

        normalized = self._normalize_components(values)
        if not any(normalized.values()):
            raise ValidationError({"address": "A replacement address must not be empty."})
        with transaction.atomic(), actor_context(actor):
            self.lock_party(party.pk)
            current = (
                self.sudo(reason="parties.address.replace_primary_exact")
                .lock_if_supported()
                .filter(
                    party=party,
                    is_primary=True,
                )
                .first()
            )
            if (current.pk if current else None) != expected_id:
                raise ValidationError({"address": "The party's primary address changed during review."})
            if current is None:
                status, created = self.attach_exact(
                    party=party,
                    values=normalized,
                    actor=actor,
                    label=label,
                    is_primary=True,
                )
                if created is None:  # pragma: no cover - non-empty values cannot be missing
                    raise ValidationError({"address": "The replacement address could not be created."})
                return status, created
            if not current.with_actor(actor).has_access("write"):
                raise PermissionDenied("write access to the primary party address is required")
            changed = [field for field, value in normalized.items() if getattr(current, field) != value]
            normalized_label = " ".join(label.split()).strip()[:64]
            if current.label != normalized_label:
                current.label = normalized_label
                changed.append("label")
            for field in changed:
                if field != "label":
                    setattr(current, field, normalized[field])
            if changed:
                current.save(update_fields=[*changed, "updated_at"])
                return "replaced", current.with_actor(actor)
            return "matched", current.with_actor(actor)


class Address(TrashMixin, PartyAspectMixin, HistoryMixin, AuditMixin, AngeeDataModel):
    """A physical or postal address of a party (the vCard ``ADR`` property).

    There is intentionally no ``(party, label)`` uniqueness — a party may carry
    two same-labelled addresses — so a CardDAV mapper keys idempotency on the
    address content, not the label.
    """

    runtime = True

    sqid_prefix = "adr_"
    statements = StatementRecords(
        link="integrate.RecordLink",
        aspects={
            "address": Aspect(
                fields=(
                    "label",
                    "po_box",
                    "extended",
                    "street",
                    "city",
                    "region",
                    "postal_code",
                    "country",
                ),
                precedence=cast(
                    tuple[SourceKind, ...], (SourceKind.PERSON, SourceKind.SYSTEM_OF_RECORD, SourceKind.DIRECTORY)
                ),
            )
        },
        sqid_prefix="ads_",
    )
    party = models.ForeignKey(
        "parties.Party",
        on_delete=models.CASCADE,
        related_name="addresses",
    )
    label = models.CharField(max_length=64, blank=True, default="")
    po_box = models.CharField(max_length=128, blank=True, default="")
    extended = models.TextField(blank=True, default="")
    street = models.TextField(blank=True, default="")
    city = models.TextField(blank=True, default="")
    region = models.TextField(blank=True, default="")
    postal_code = models.CharField(max_length=32, blank=True, default="")
    country = CountryCodeField(blank=True, default="")
    latitude = models.FloatField(null=True, blank=True)
    longitude = models.FloatField(null=True, blank=True)
    is_primary = models.BooleanField(default=False)
    objects = AddressManager()

    class Meta:
        """Django model options for the address source model."""

        abstract = True
        ordering = ("party", "label", "sqid")
        verbose_name_plural = "addresses"
        rebac_resource_type = "parties/address"
        constraints = (
            models.UniqueConstraint(
                fields=("party",),
                condition=models.Q(is_primary=True),
                name="parties_address_one_primary_per_party",
            ),
        )

    def save(self, *args: Any, **kwargs: Any) -> None:
        """Make the selected primary address authoritative for this party."""

        update_fields = kwargs.get("update_fields")
        if not self.is_primary or (update_fields is not None and "is_primary" not in update_fields):
            super().save(*args, **kwargs)
            return
        with transaction.atomic():
            type(self).objects.demote_primaries(self)
            super().save(*args, **kwargs)

    def __str__(self) -> str:
        """Return a one-line address for Django displays."""

        return ", ".join(part for part in (self.street, self.city, self.country) if part)


class Folder(OwnerMixin, AngeeDataModel):
    """A group of parties — the local mirror of a synced address book.

    The contacts counterpart of storage's ``Drive``/``Folder`` and knowledge's
    ``Vault`` container idea, kept to exactly what sync needs today: the directory
    it mirrors, the collection ``source_href`` (one folder per ``(directory,
    source_href)`` makes the folder upsert idempotent). Sync progress belongs to
    ``integrate.SyncStream``. Owned through its transferable ``owner``, which a
    sync's insert takes from the directory owner it stamps as ``created_by``;
    deleting a folder leaves its linked parties. Manual creation and a folder
    tree (``parent``) are deferred until a create path lands to exercise them.
    """

    runtime = True

    sqid_prefix = "fol_"
    name = models.CharField(max_length=200)
    directory = models.ForeignKey(
        "parties.Directory",
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name="folders",
    )
    source_href = models.CharField(max_length=1024, blank=True, default="")

    objects = AngeeManager()

    class Meta:
        """Django model options for the folder source model."""

        abstract = True
        ordering = ("name", "sqid")
        rebac_resource_type = "parties/folder"
        constraints = (
            models.UniqueConstraint(
                fields=("directory", "source_href"),
                condition=~models.Q(source_href=""),
                name="uq_folder_directory_source",
            ),
        )

    def __str__(self) -> str:
        """Return the folder name for Django displays."""

        return self.name


class Circle(HierarchyMixin, OwnerMixin, AngeeDataModel):
    """A private, overlapping grouping of parties — how the owner organises people.

    Circles are the curated counterpart of :class:`Folder` (which mirrors a synced
    address book): "Family", "Inner Circle", a climbing crew. A party may belong to
    many circles (:class:`CircleMember`), and circles nest as a tree — overlap
    comes from multi-membership, never from multiple parents. The tree is
    :class:`~angee.models.HierarchyMixin`'s materialized path, so "everyone in
    this circle including sub-circles" is an indexed prefix scan
    (``Circle.objects.subtree_of(circle)``), and ``hierarchy_scope_fields`` keeps a
    personal circle tree from straddling owners: the transferable ``owner`` scopes
    the tree, so a transfer never leaves a circle's parent or children under
    another owner.

    Circles are an organising surface only — they never gate visibility or
    sharing; REBAC stays the one authorization owner.
    """

    runtime = True
    sqid_prefix = "cir_"
    hierarchy_scope_fields = ("owner",)

    name = models.CharField(max_length=200)
    description = models.TextField(blank=True, default="")
    color = ColorField(max_length=32, blank=True, default="")
    """Display colour for chips/dots; presentation only."""

    icon = models.CharField(max_length=128, blank=True, default="")
    """Icon registry name for navigation; presentation only."""

    position = models.PositiveIntegerField(default=0)

    objects = CircleManager()

    class Meta(HierarchyMixin.Meta):
        """Django model options carrying the hierarchy path index."""

        abstract = True
        ordering = ("position", "name", "sqid")
        rebac_resource_type = "parties/circle"

    def __str__(self) -> str:
        """Return the circle name for Django displays."""

        return self.name


class CircleMember(MembershipDecisionMixin, AuditMixin, AngeeDataModel):
    """A party's reviewable membership of one circle.

    Membership is a plain decision row so a suggester (community detection, an
    org-domain rule, an LLM) can propose a weak membership for review instead of
    silently filing people; a human decision records acceptance at full confidence.
    One row per ``(circle, party)`` — re-suggesting an existing membership updates
    the row rather than duplicating it.
    """

    runtime = True
    sqid_prefix = "cme_"

    circle = models.ForeignKey(
        "parties.Circle",
        on_delete=models.CASCADE,
        related_name="members",
    )
    party = models.ForeignKey(
        "parties.Party",
        on_delete=models.CASCADE,
        related_name="circle_members",
    )

    class Meta:
        """Django model options for the circle-membership source model."""

        abstract = True
        ordering = ("circle", "sqid")
        rebac_resource_type = "parties/circle_member"
        constraints = (
            models.UniqueConstraint(
                fields=("circle", "party"),
                name="uq_circle_member",
            ),
        )

    def __str__(self) -> str:
        """Return a readable membership description for Django displays."""

        return f"{self.party_id}∈{self.circle_id}"


class RelationshipKind(AuditMixin, AngeeDataModel):
    """The relationship vocabulary — types as catalogue data, never schema.

    One row expresses both directions of an asymmetric type through
    ``inverse_name`` ("Parent" / "Child"); a blank inverse means the type is
    symmetric ("Friend"). The master tier seeds the XFN / vCard ``RELATED``
    vocabulary (adopted by slug, so a project may rename labels without forking
    rows), and users may add their own kinds alongside.
    """

    runtime = True
    catalogue = True
    catalogue_tier = "master"
    sqid_prefix = "rkd_"

    class RelationshipCategory(models.TextChoices):
        """The XFN category a relationship kind belongs to.

        Schema-unique class name — enum class names project as global GraphQL
        enum names.
        """

        FAMILY = "family", "Family"
        FRIENDSHIP = "friendship", "Friendship"
        ROMANTIC = "romantic", "Romantic"
        PROFESSIONAL = "professional", "Professional"
        GEOGRAPHICAL = "geographical", "Geographical"
        OTHER = "other", "Other"

    class PartyKind(models.TextChoices):
        """The concrete party kind an edge end must be for a kind to be legal.

        ``any`` places no constraint; ``person`` / ``organization`` require that end
        to be that concrete :class:`Party` child (employment kinds require an
        organisation counterparty). Schema-unique class name — projects as a global
        GraphQL enum.
        """

        PERSON = "person", "Person"
        ORGANIZATION = "organization", "Organization"
        ANY = "any", "Any"

        def model(self) -> type[models.Model] | None:
            """Return the explicitly mapped concrete party model for this kind."""

            label = {
                type(self).PERSON: "parties.Person",
                type(self).ORGANIZATION: "parties.Organization",
                type(self).ANY: None,
            }[self]
            return apps.get_model(label) if label is not None else None

    slug = models.SlugField(unique=True)
    name = models.CharField(max_length=128)
    """Anchor-side label: what the counterparty is *to the anchor* ("Mother")."""

    inverse_name = models.CharField(max_length=128, blank=True, default="")
    """Counterparty-side label ("Child"); blank means the kind is symmetric."""

    category = StateField(choices_enum=RelationshipCategory, default=RelationshipCategory.OTHER)
    party_kind = StateField(choices_enum=PartyKind, default=PartyKind.ANY)
    """Concrete kind the anchor (:attr:`Relationship.party`) must be, or ``any``."""

    other_party_kind = StateField(choices_enum=PartyKind, default=PartyKind.ANY)
    """Concrete kind the counterparty (:attr:`Relationship.other_party`) must be, or ``any``."""

    class Meta:
        """Django model options for the relationship-kind catalogue."""

        abstract = True
        ordering = ("slug",)
        rebac_resource_type = "parties/relationship_kind"

    def __str__(self) -> str:
        """Return the kind's forward label for Django displays."""

        return self.name

    @classmethod
    def employee(cls) -> RelationshipKind | None:
        """Return the employment vocabulary row when this installation provides it."""

        return cls._base_manager.filter(slug="employee").first()

    @property
    def is_symmetric(self) -> bool:
        """Whether the kind reads the same in both directions."""

        return not self.inverse_name

    def label_for(self, *, outbound: bool) -> str:
        """Return the label as seen from one side of the edge.

        ``outbound=True`` is the anchor's side — on Maya's card her mother's row
        renders ``name`` ("Mother"); the counterparty's card renders the inverse
        ("Child": Maya is the mother's child), falling back to the forward name
        for a symmetric kind.
        """

        return self.name if outbound or self.is_symmetric else self.inverse_name

    def validate_ends(
        self,
        party: Party | None,
        other_party: Party | None,
    ) -> None:
        """Raise :class:`ValidationError` if an edge's ends violate this kind's legality.

        The knowledge-level guard: an ``organization``-typed end must be a tracked
        organisation-kind party, a ``person``-typed end a person-kind party. A
        free-text (untracked) counterparty is unconstrained — its kind is unknown —
        so an employment kind still records a person's employer by free-text name.
        """

        requirements = (
            ("party", self.PartyKind(self.party_kind), party),
            ("other_party", self.PartyKind(self.other_party_kind), other_party),
        )
        checked = tuple(
            (field, required, end)
            for field, required, end in requirements
            if required != self.PartyKind.ANY and end is not None
        )
        if not checked:
            return
        relation_names: set[str] = set()
        for _field, required, _end in checked:
            model = required.model()
            if model is not None:
                model_name = model._meta.model_name
                assert model_name is not None
                relation_names.add(model_name)
        party_model = apps.get_model("parties", "Party")
        concrete_by_pk = {
            row["pk"]: row
            for row in party_model._base_manager.filter(pk__in={end.pk for _field, _required, end in checked}).values(
                "pk", *sorted(relation_names)
            )
        }
        errors: dict[str, str] = {}
        for field, required, end in checked:
            self._validate_end(field, required, end, concrete_by_pk, errors)
        if errors:
            raise ValidationError(errors)

    def _validate_end(
        self,
        field: str,
        required: PartyKind,
        party: Party,
        concrete_by_pk: dict[Any, dict[str, Any]],
        errors: dict[str, str],
    ) -> None:
        """Add one end error using the shared concrete-kind query result."""

        model = required.model()
        if model is None:
            return
        row = concrete_by_pk.get(party.pk, {})
        model_name = model._meta.model_name
        assert model_name is not None
        if row.get(model_name) is None:
            errors[field] = f"{self.name} requires {required} on this end."


class Relationship(TrashMixin, PartyAspectMixin, HistoryMixin, AuditMixin, AngeeDataModel):
    """A typed edge from one party's viewpoint: the *other* is ``kind`` of ``party``.

    ``kind.name`` names what the counterparty is to the anchor ("Mother",
    "Mentor", "Colleague"); the counterparty's own card renders the reverse
    through :meth:`RelationshipKind.label_for` ("Child", "Mentee"). A single row
    carries both readings (Monica's Chandler shape — the mirror-row scheme was
    abandoned there for drifting). The counterparty is a tracked :class:`Party`
    when known, falling back to free-text ``other_name`` so a family-history
    relative — or a person's employer parsed from a vCard — who is not a directory
    entry still records. Edges are time-bounded (party-model ``from``/``thru``), so
    "was my colleague 2019–2022" stays queryable after it ends; an open edge has no
    ``ended_at``. ``title`` carries the vCard ``TITLE`` ("CTO", "Godmother of").
    """

    runtime = True
    objects = StatementSubjectManager()
    sqid_prefix = "rel_"
    statements = StatementRecords(
        link="integrate.RecordLink",
        aspects={
            "relationship": Aspect(
                fields=(
                    "other_name",
                    "title",
                    "started_at",
                    "ended_at",
                    "notes",
                ),
                precedence=cast(tuple[SourceKind, ...], (SourceKind.PERSON, SourceKind.DIRECTORY, SourceKind.IMPORT)),
            )
        },
        sqid_prefix="rls_",
    )

    party = models.ForeignKey(
        "parties.Party",
        on_delete=models.CASCADE,
        related_name="relationships",
    )
    """The anchor — the contact whose card this fact lives on."""

    other_party = models.ForeignKey(
        "parties.Party",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="inbound_relationships",
    )
    other_name = models.TextField(blank=True, default="")
    """Free-text counterparty when the relative is not a tracked party.

    Unbounded free text like its sibling ``title``/``notes``: a synced vCard
    ``ORG`` can legitimately list many affiliated entities in one value, which a
    fixed ``varchar`` truncates or rejects.
    """

    kind = models.ForeignKey(
        "parties.RelationshipKind",
        on_delete=models.PROTECT,
        related_name="relationships",
    )
    title = models.TextField(blank=True, default="")
    """Role title on this edge — the vCard ``TITLE`` ("CTO", "Godmother of")."""

    started_at = models.DateField(null=True, blank=True)
    ended_at = models.DateField(null=True, blank=True)
    notes = models.TextField(blank=True, default="")

    class Meta:
        """Django model options for the relationship source model."""

        abstract = True
        ordering = ("party", "sqid")
        rebac_resource_type = "parties/relationship"
        constraints = (
            # One row per tracked pair per kind; free-text counterparties are
            # unconstrained (two untracked "Cousin" rows are legitimate).
            models.UniqueConstraint(
                fields=("party", "other_party", "kind"),
                condition=models.Q(other_party__isnull=False),
                name="uq_relationship_edge",
            ),
            models.CheckConstraint(
                condition=models.Q(other_party__isnull=True) | ~models.Q(party=models.F("other_party")),
                name="ck_relationship_distinct_parties",
            ),
            # The DB owns "every edge names its counterparty" — a tracked party
            # or at least a free-text name.
            models.CheckConstraint(
                condition=models.Q(other_party__isnull=False) | ~models.Q(other_name=""),
                name="ck_relationship_has_other",
            ),
        )

    def __str__(self) -> str:
        """Return a readable edge description for Django displays."""

        return f"{self.party_id}←{self.kind_id}: {self.other_name or self.other_party_id}"

    def clean(self) -> None:
        """Enforce the kind's end legality so the Hasura ``full_clean`` create path surfaces it."""

        super().clean()
        if self.kind_id is not None:
            self.kind.validate_ends(self.party, self.other_party)

    def save(self, *args: Any, **kwargs: Any) -> None:
        """Persist the edge, validating ends only when the write can change them."""

        update_fields = kwargs.get("update_fields")
        end_fields = {"party", "party_id", "other_party", "other_party_id", "kind", "kind_id"}
        if self.kind_id is not None and (update_fields is None or end_fields.intersection(update_fields)):
            party_model = type(self).party.field.related_model
            kind_model = type(self).kind.field.related_model
            kind = kind_model._base_manager.get(pk=self.kind_id)
            ends = party_model._base_manager.in_bulk([self.party_id, self.other_party_id])
            kind.validate_ends(ends.get(self.party_id), ends.get(self.other_party_id))
        super().save(*args, **kwargs)


class Directory(Bridge):
    """A connected contacts source that syncs parties from an external directory.

    An ``integrate.Integration`` child (so it draws its credential / owner / status
    from the connection substrate) and a ``Bridge`` (so the scheduler and the eager
    ``syncIntegration`` mutation drive it). ``backend_class`` selects the protocol —
    ``carddav`` (contributed by ``parties_integrate_carddav``) — and ``config``
    carries the source URL. The inherited ``Bridge.sync()`` drives the backend
    streams; the parties managers own the contact projection and ingest path.
    """

    runtime = True
    extends: ClassVar[str | None] = "integrate.Integration"
    integration_create_mode: ClassVar[IntegrationCreateMode | None] = IntegrationCreateMode.CONNECT

    @property
    def source_kind(self) -> SourceKind:
        """Contacts asserted by this integration are directory statements."""

        return cast(SourceKind, SourceKind.DIRECTORY)

    backend_class = ImplClassField(
        DirectoryBackend,
        default="manual",
        create_only=True,
    )
    """Registry key for the directory backend bound to this directory."""

    class Meta:
        """Django model options for the directory child model."""

        abstract = True
        ordering = ("-updated_at",)
        verbose_name_plural = "directories"
        rebac_resource_type = "parties/directory"

    @property
    def backend(self) -> DirectoryBackend:
        """Return this directory's selected backend, bound to this row."""

        backend_class = cast("type[DirectoryBackend]", self.resolve_impl("backend_class"))
        return backend_class(self)
