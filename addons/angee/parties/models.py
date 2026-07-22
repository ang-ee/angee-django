"""Source models for the parties addon.

Parties are the people and organisations a project keeps track of. A party is
reached through one or more :class:`Handle` rows (an email address, a phone
number, a social handle) — the same handle that messaging uses as a participant,
so this addon is the contacts foundation the messaging addon builds on. The link
between a party and a handle is itself confidence-bearing (:class:`PartyHandle`)
so a sync can record an uncertain match as a weak candidate instead of guessing.

``Party`` is a multi-table-inheritance parent; the concrete kind is the child
model (:class:`Person`, :class:`Organization`), not a column — a person carries
name parts and a link to its :class:`~angee.iam.models.User`, an organisation
carries its legal name and domain.

Parties are organised two ways, both human-owned facts: :class:`Circle` is a
private, overlapping grouping (a party may belong to many circles; circles nest
as a :class:`~angee.base.mixins.HierarchyMixin` tree), and :class:`Relationship`
is a typed, directed party↔party edge whose vocabulary
(:class:`RelationshipKind`) is catalogue data seeded from the XFN / vCard
``RELATED`` values — never a schema fact.
"""

from __future__ import annotations

from collections.abc import Mapping
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
from rebac import PermissionDenied, system_context
from rebac.managers import RebacManager

from angee.base.fields import SqidField, StateField
from angee.base.impl import ImplClassField
from angee.base.mixins import AuditMixin, HierarchyMixin, SqidMixin
from angee.base.models import AngeeManager, AngeeModel
from angee.integrate.models import Bridge
from angee.parties.backends import DirectoryBackend
from angee.parties.managers import (
    CircleManager,
    HandleManager,
    MergeVetoManager,
    PartyHandleManager,
    PartyManager,
)
from angee.parties.mixins import LinkSource, ScoredLinkMixin


class Party(SqidMixin, AuditMixin, AngeeModel):
    """A person or organisation the project tracks.

    The parent owns the common contact identity — the public id, ownership, the
    display name, avatar, notes, and the lossless-vCard carriers. The concrete
    kind (and its kind-specific fields) lives on the :class:`Person` /
    :class:`Organization` child row.
    """

    runtime = True

    sqid = SqidField(real_field_name="id", prefix="pty_", min_length=8)
    display_name = models.TextField()
    notes = models.TextField(blank=True, default="")
    avatar = models.ForeignKey(
        "storage.File",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="+",
    )
    handle_count = models.PositiveIntegerField(default=0, db_index=True)
    merged_into = models.ForeignKey(
        "self",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="merged_from",
    )
    raw_vcard = models.TextField(blank=True, default="")
    extensions = models.JSONField(blank=True, default=dict)
    folder = models.ForeignKey(
        "parties.Folder",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="parties",
    )
    source_uid = models.CharField(max_length=512, blank=True, default="")
    source_etag = models.CharField(max_length=512, blank=True, default="")
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
        rebac_resource_type = "parties/party"
        rebac_id_attr = "sqid"
        constraints = (
            # The directory-sync idempotency key: one party per source UID per
            # folder, so re-sync updates the same row instead of duplicating.
            models.UniqueConstraint(
                fields=("folder", "source_uid"),
                condition=~models.Q(source_uid=""),
                name="uq_party_folder_source_uid",
            ),
            models.CheckConstraint(
                condition=~models.Q(merged_into=models.F("id")),
                name="ck_party_not_merged_into_self",
            ),
        )

    def __str__(self) -> str:
        """Return the party's display name for Django displays."""

        return self.display_name

    @property
    def concrete_kind(self) -> str | None:
        """Return this party's concrete kind (``"person"`` / ``"organization"``), or ``None``.

        A structural fact read straight from the multi-table-inheritance child rows
        that share this party's primary key — not a stored column (the child model
        *is* the kind). Read through the base manager: it is a system integrity fact,
        never actor-scoped user data.
        """

        for kind in (
            cast(RelationshipKind.PartyKind, RelationshipKind.PartyKind.ORGANIZATION),
            cast(RelationshipKind.PartyKind, RelationshipKind.PartyKind.PERSON),
        ):
            child = kind.model()
            if child is not None and child._base_manager.filter(pk=self.pk).exists():
                return str(kind)
        return None

    def canonical(self) -> Party:
        """Return the surviving party this one resolves to, following merge pointers.

        :meth:`merge_into` flattens normal writes to the terminal, but this method
        still follows a longer chain and guards a cycle so legacy/corrupt data
        cannot loop forever during a read.
        """

        seen = {self.pk}
        party = self
        while party.merged_into_id is not None and party.merged_into_id not in seen:
            seen.add(party.merged_into_id)
            party = party.merged_into
        return party

    def apply_merge_field_overrides(self, source: Party, field_overrides: Any) -> None:
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
            person.sudo(reason="parties.merge.person_field_overrides")
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

    def merge_into(self, target: Party) -> Party:
        """Atomically merge this party into ``target`` and return the terminal target.

        The source chain head is row-locked through ``lock_if_supported`` before
        its pointer changes. The target resolves defensively to its canonical
        terminal, and existing rows that pointed at the source are repointed in the
        same transaction so normal merge chains stay one hop deep. Reversing an
        existing merge raises :class:`ValidationError` instead of silently clearing
        either pointer.
        """

        if self.pk is None or target.pk is None:
            raise ValidationError({"merged_into": "Both parties must be saved before merging."})
        party_model = apps.get_model("parties", "Party")
        with transaction.atomic():
            source = party_model.objects.lock_if_supported().get(pk=self.pk)
            target_head = party_model.objects.lock_if_supported().get(pk=target.pk)
            terminal = target_head.canonical()
            if terminal.pk == source.pk:
                raise ValidationError(
                    {"merged_into": "Cannot reverse a merge by merging its target back into the source."}
                )
            source.merged_into = terminal
            source.save(update_fields=["merged_into", "updated_at"])
            party_model._base_manager.filter(merged_into_id=source.pk).exclude(pk=source.pk).update(
                merged_into_id=terminal.pk
            )
        self.merged_into = terminal
        self.merged_into_id = terminal.pk
        return cast(Party, terminal)


class Person(AngeeModel):
    """A human party — carries name parts and an optional platform-user link."""

    runtime = True
    extends = "parties.Party"

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
        rebac_resource_type = "parties/person"
        rebac_id_attr = "sqid"


class MergeVeto(SqidMixin, AuditMixin, AngeeModel):
    """A durable decision that two canonically ordered parties must stay separate."""

    runtime = True
    sqid_prefix = "mvt_"

    party_a = models.ForeignKey(
        "parties.Party",
        on_delete=models.CASCADE,
        related_name="merge_vetoes_as_a",
    )
    party_b = models.ForeignKey(
        "parties.Party",
        on_delete=models.CASCADE,
        related_name="merge_vetoes_as_b",
    )

    objects = MergeVetoManager()

    class Meta:
        """Django model options for the canonical keep-separate pair."""

        abstract = True
        ordering = ("party_a", "party_b", "sqid")
        rebac_resource_type = "parties/merge_veto"
        rebac_id_attr = "sqid"
        constraints = (
            models.CheckConstraint(
                condition=models.Q(party_a__lt=models.F("party_b")),
                name="ck_merge_veto_party_order",
            ),
            models.UniqueConstraint(
                fields=("party_a", "party_b"),
                name="uq_merge_veto_party_pair",
            ),
        )

    def __str__(self) -> str:
        """Return the canonical pair for Django displays."""

        return f"merge-veto:{self.party_a_id}<->{self.party_b_id}"

    def save(self, *args: Any, **kwargs: Any) -> None:
        """Persist the veto in canonical primary-key order."""

        party_a_id = getattr(self, "party_a_id", None)
        party_b_id = getattr(self, "party_b_id", None)
        if party_a_id is not None and party_b_id is not None and party_a_id > party_b_id:
            party_a = self._state.fields_cache.pop("party_a", None)
            party_b = self._state.fields_cache.pop("party_b", None)
            self.party_a_id = party_b_id
            self.party_b_id = party_a_id
            if party_b is not None:
                self.party_a = party_b
            if party_a is not None:
                self.party_b = party_a
            update_fields = kwargs.get("update_fields")
            if update_fields is not None:
                kwargs["update_fields"] = {*update_fields, "party_a", "party_b"}
        super().save(*args, **kwargs)


class Organization(AngeeModel):
    """An organisation party — carries its legal name and primary domain."""

    runtime = True
    extends = "parties.Party"

    legal_name = models.TextField(blank=True, default="")
    domain = models.CharField(max_length=255, blank=True, default="", db_index=True)

    objects = AngeeManager()

    class Meta:
        """Django model options for the organization child model."""

        abstract = True
        rebac_resource_type = "parties/organization"
        rebac_id_attr = "sqid"


class Handle(SqidMixin, AuditMixin, AngeeModel):
    """A reachable address or handle of a party on one platform.

    Keyed on ``(platform, value)`` and, when present, ``(platform, external_id)``
    — those unique constraints are the ingestion-dedup keys that make re-sync
    idempotent. ``party`` is the resolved owner the :class:`PartyHandle` manager
    materialises; it is null until a handle is linked, so a handle synced for an
    unknown sender is still a valid row.
    """

    runtime = True

    class Platform(models.TextChoices):
        """The kind of channel a handle reaches a party through."""

        EMAIL = "email", "Email"
        PHONE = "phone", "Phone"
        MATRIX = "matrix", "Matrix"
        SIGNAL = "signal", "Signal"
        SLACK = "slack", "Slack"
        TELEGRAM = "telegram", "Telegram"
        WHATSAPP = "whatsapp", "WhatsApp"
        YOUTUBE = "youtube", "YouTube"
        FACEBOOK = "facebook", "Facebook"
        OTHER = "other", "Other"

        @classmethod
        def for_value(cls, value: str) -> Handle.Platform:
            """Classify a raw handle value through the one platform heuristic."""

            return cast(Handle.Platform, cls.EMAIL if "@" in (value or "") else cls.OTHER)

    sqid = SqidField(real_field_name="id", prefix="hdl_", min_length=8)
    platform = StateField(choices_enum=Platform, default=Platform.EMAIL)
    value = models.CharField(max_length=512)
    normalized_value = models.CharField(max_length=512, db_index=True, editable=False)
    external_id = models.CharField(max_length=512, blank=True, default="")
    display_name = models.CharField(max_length=4096, blank=True, default="")
    label = models.CharField(max_length=64, blank=True, default="")
    is_preferred = models.BooleanField(default=False)
    owner = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="own_handles",
        db_index=True,
    )
    """The user who controls this address — sends, posts, or syncs as it.

    The **control** fact, distinct from the **identity** fact (``party`` — who the
    address reaches, resolved from :class:`PartyHandle`). A shared team inbox or a
    service account may carry an ``owner`` without resolving to that user's party,
    and an ex-address may reach a person's party without anyone controlling it.
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

    objects = HandleManager()

    class Meta:
        """Django model options for the handle source model."""

        abstract = True
        ordering = ("platform", "value", "sqid")
        rebac_resource_type = "parties/handle"
        rebac_id_attr = "sqid"
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
        punctuation still does not fork the same contact point.
        """

        normalized = (value or "").strip().lower()
        if platform == cls.Platform.EMAIL and "@" in normalized:
            local, _, domain = normalized.rpartition("@")
            if domain in ("gmail.com", "googlemail.com"):
                local = local.split("+", 1)[0].replace(".", "")
            return f"{local}@{domain}"
        if platform in (cls.Platform.PHONE, cls.Platform.WHATSAPP):
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

    @property
    def resolved_confidence(self) -> float | None:
        """Confidence of the link that resolved this handle's owner.

        ``party`` is materialised from the winning :class:`PartyHandle` (see
        :meth:`PartyHandleManager.resolve`) and ``(party, handle)`` is unique, so
        the link matching the resolved ``party`` is that winner — its score is the
        resolution confidence. ``None`` when the handle is unowned or, under
        actor-scoped loading, the resolving link is not readable by the actor.
        """

        if self.party_id is None:
            return None
        for link in self.party_links.all():
            if link.party_id == self.party_id:
                return link.confidence
        return None


class PartyHandle(ScoredLinkMixin, SqidMixin, AuditMixin, AngeeModel):
    """A confidence-bearing link between a party and one of its handles.

    A handle may carry several scored candidate parties (:class:`ScoredLinkMixin`),
    so a sync can surface an uncertain match as a weak link (a conflicting claim is
    recorded at ``0.3`` confidence) instead of silently reassigning. The resolved
    owner is the highest-confidence, non-dismissed link — the value the manager
    materialises onto :attr:`Handle.party`.
    """

    runtime = True

    sqid = SqidField(real_field_name="id", prefix="phl_", min_length=8)
    party = models.ForeignKey(
        "parties.Party",
        on_delete=models.CASCADE,
        related_name="party_handles",
    )
    handle = models.ForeignKey(
        "parties.Handle",
        on_delete=models.CASCADE,
        related_name="party_links",
    )
    metadata = models.JSONField(blank=True, default=dict)

    objects = PartyHandleManager()

    class Meta:
        """Django model options for the party-handle link source model."""

        abstract = True
        ordering = ("-is_confirmed", "-confidence", "sqid")
        rebac_resource_type = "parties/party_handle"
        rebac_id_attr = "sqid"
        constraints = (
            models.UniqueConstraint(
                fields=("party", "handle"),
                name="uq_party_handle",
            ),
        )

    def __str__(self) -> str:
        """Return a readable link description for Django displays."""

        return f"{self.party_id}↔{self.handle_id} ({self.confidence})"

    def confirm(self) -> None:
        """Human-confirm this link, then re-resolve the handle's owner.

        Overrides the plain :meth:`ScoredLinkMixin.confirm` to add the two facts a
        contacts confirmation carries that the generic mixin must not: the actor must
        hold ``write`` on the link, and the resolution cascade (the handle's owner
        pointer, both parties' counts) is server-owned bookkeeping that runs elevated.
        """

        if not self.has_access("write"):
            raise PermissionDenied("write access to the party-handle link is required")
        with system_context(reason="parties.party_handle.confirm"):
            super().confirm()

    def dismiss(self) -> None:
        """Dismiss this link — the durable anti-link — then re-resolve the handle.

        Gated and elevated like :meth:`confirm`; the mixin flips the flags and calls
        :meth:`_resolve_link`, which demotes the handle to its next candidate or to
        unowned.
        """

        if not self.has_access("write"):
            raise PermissionDenied("write access to the party-handle link is required")
        with system_context(reason="parties.party_handle.dismiss"):
            super().dismiss()

    def _resolve_link(self) -> None:
        """Re-materialise :attr:`Handle.party` from this handle's surviving links."""

        type(self).objects.resolve(self.handle)


class Address(SqidMixin, AuditMixin, AngeeModel):
    """A physical or postal address of a party (the vCard ``ADR`` property).

    There is intentionally no ``(party, label)`` uniqueness — a party may carry
    two same-labelled addresses — so a CardDAV mapper keys idempotency on the
    address content, not the label.
    """

    runtime = True

    sqid = SqidField(real_field_name="id", prefix="adr_", min_length=8)
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
    country = models.TextField(blank=True, default="")
    latitude = models.FloatField(null=True, blank=True)
    longitude = models.FloatField(null=True, blank=True)
    is_primary = models.BooleanField(default=False)

    class Meta:
        """Django model options for the address source model."""

        abstract = True
        ordering = ("party", "label", "sqid")
        rebac_resource_type = "parties/address"
        rebac_id_attr = "sqid"

    def __str__(self) -> str:
        """Return a one-line address for Django displays."""

        return ", ".join(part for part in (self.street, self.city, self.country) if part)


class Folder(SqidMixin, AuditMixin, AngeeModel):
    """A group of parties — the local mirror of a synced address book.

    The contacts counterpart of storage's ``Drive``/``Folder`` and knowledge's
    ``Vault`` container idea, kept to exactly what sync needs today: the directory
    it mirrors, the collection ``source_href`` (one folder per ``(directory,
    source_href)`` makes the folder upsert idempotent), and the incremental cursors
    (``ctag`` / ``sync_token``). Owned via ``created_by``; deleting a folder leaves
    its parties (``SET_NULL`` on :attr:`Party.folder`). Manual creation and a folder
    tree (``parent``) are deferred until a create path lands to exercise them.
    """

    runtime = True

    sqid = SqidField(real_field_name="id", prefix="fol_", min_length=8)
    name = models.CharField(max_length=200)
    directory = models.ForeignKey(
        "parties.Directory",
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name="folders",
    )
    source_href = models.CharField(max_length=1024, blank=True, default="")
    ctag = models.CharField(max_length=512, blank=True, default="")
    sync_token = models.TextField(blank=True, default="")

    objects = AngeeManager()

    class Meta:
        """Django model options for the folder source model."""

        abstract = True
        ordering = ("name", "sqid")
        rebac_resource_type = "parties/folder"
        rebac_id_attr = "sqid"
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


class Circle(HierarchyMixin, SqidMixin, AuditMixin, AngeeModel):
    """A private, overlapping grouping of parties — how the owner organises people.

    Circles are the curated counterpart of :class:`Folder` (which mirrors a synced
    address book): "Family", "Inner Circle", a climbing crew. A party may belong to
    many circles (:class:`CircleMember`), and circles nest as a tree — overlap
    comes from multi-membership, never from multiple parents. The tree is
    :class:`~angee.base.mixins.HierarchyMixin`'s materialized path, so "everyone in
    this circle including sub-circles" is an indexed prefix scan
    (``Circle.objects.subtree_of(circle)``), and ``hierarchy_scope_fields`` keeps a
    personal circle tree from straddling owners.

    Circles are an organising surface only — they never gate visibility or
    sharing; REBAC stays the one authorization owner.
    """

    runtime = True
    sqid_prefix = "cir_"
    hierarchy_scope_fields = ("created_by",)

    name = models.CharField(max_length=200)
    description = models.TextField(blank=True, default="")
    color = models.CharField(max_length=32, blank=True, default="")
    """Display color token or hex for chips/dots; presentation only."""

    icon = models.CharField(max_length=128, blank=True, default="")
    """Icon registry name for navigation; presentation only."""

    position = models.PositiveIntegerField(default=0)

    objects = CircleManager()

    class Meta(HierarchyMixin.Meta):
        """Django model options carrying the hierarchy path index."""

        abstract = True
        ordering = ("position", "name", "sqid")
        rebac_resource_type = "parties/circle"
        rebac_id_attr = "sqid"

    def __str__(self) -> str:
        """Return the circle name for Django displays."""

        return self.name


class CircleMember(ScoredLinkMixin, SqidMixin, AuditMixin, AngeeModel):
    """A party's membership of one circle, scored like a :class:`PartyHandle` link.

    Membership is a :class:`ScoredLinkMixin` so a suggester (community detection, an
    org-domain rule, an LLM) can propose a weak membership for review instead of
    silently filing people; a human decision writes ``manual`` at full confidence.
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
        rebac_id_attr = "sqid"
        constraints = (
            models.UniqueConstraint(
                fields=("circle", "party"),
                name="uq_circle_member",
            ),
        )

    def __str__(self) -> str:
        """Return a readable membership description for Django displays."""

        return f"{self.party_id}∈{self.circle_id}"


class RelationshipKind(SqidMixin, AuditMixin, AngeeModel):
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
        rebac_id_attr = "sqid"

    def __str__(self) -> str:
        """Return the kind's forward label for Django displays."""

        return self.name

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

    def validate_ends(self, party: Party | None, other_party: Party | None) -> None:
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
                relation_names.add(model._meta.model_name)
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
        if row.get(model._meta.model_name) is None:
            errors[field] = f"{self.name} requires {required} on this end."


class Relationship(SqidMixin, AuditMixin, AngeeModel):
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
    sqid_prefix = "rel_"

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
    source = StateField(choices_enum=LinkSource, default=LinkSource.MANUAL)
    """Provenance of the edge; sync-owned rows stay distinct from human rows."""

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
        rebac_id_attr = "sqid"
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
            models.UniqueConstraint(
                fields=("party", "kind", "source"),
                condition=models.Q(
                    other_party__isnull=True,
                    source=LinkSource.CARDDAV,
                ),
                name="uq_relationship_carddav_employment",
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
            self.kind.validate_ends(self.party, self.other_party)
        super().save(*args, **kwargs)


class Directory(Bridge):
    """A connected contacts source that syncs parties from an external directory.

    An ``integrate.Integration`` child (so it draws its credential / owner / status
    from the connection substrate) and a ``Bridge`` (so the scheduler and the eager
    ``syncIntegration`` mutation drive it). ``backend_class`` selects the protocol —
    ``carddav`` (contributed by ``parties_integrate_carddav``) — and ``config``
    carries the source URL. ``sync()`` fetches + parses the source, then maps each
    contact onto the parties managers.
    """

    runtime = True
    extends = "integrate.Integration"
    integration_kind_label = "Directory"

    backend_class = ImplClassField(
        base_class=DirectoryBackend,
        registry_setting="ANGEE_DIRECTORY_BACKEND_CLASSES",
        default="manual",
    )
    """Registry key for the directory backend bound to this directory."""

    objects = RebacManager()

    class Meta:
        """Django model options for the directory child model."""

        abstract = True
        rebac_resource_type = "parties/directory"
        rebac_id_attr = "sqid"

    @property
    def backend(self) -> DirectoryBackend:
        """Return this directory's selected backend, bound to this row."""

        backend_class = cast("type[DirectoryBackend]", self.resolve_impl("backend_class"))
        return backend_class(self)

    def sync(self) -> int:
        """Discover address books and resolve every contact into parties (the Bridge contract).

        Idempotent: each address book mirrors to one :class:`Folder` (keyed by its
        ``source_href``), every contact upserts by ``(folder, source_uid)``, and a
        contact that vanished from the source is purged from its folder — so a
        re-sync converges to the source instead of duplicating it. A collection whose
        ``ctag`` is unchanged is skipped wholesale.
        """

        folder_model = apps.get_model("parties", "Folder")
        party_model = apps.get_model("parties", "Party")
        backend = self.backend
        resolved = 0
        for book in backend.discover():
            folder, _created = folder_model.objects.update_or_create(
                directory=self,
                source_href=book.href,
                defaults={
                    "name": book.name,
                    "created_by_id": self.owner_id,
                },
            )
            if folder.ctag and folder.ctag == book.ctag:
                continue
            seen: set[str] = set()
            for parsed in backend.fetch_contacts(book):
                if not parsed.uid:
                    continue  # no stable per-folder key → cannot upsert idempotently
                party_model.objects.ingest_contact(
                    parsed,
                    folder=folder,
                    created_by_id=self.owner_id,
                )
                seen.add(parsed.uid)
                resolved += 1
            party_model.objects.purge_missing(folder=folder, keep_uids=seen)
            folder.ctag = book.ctag
            folder.sync_token = book.sync_token
            folder.save(update_fields=["ctag", "sync_token", "updated_at"])
        return resolved
