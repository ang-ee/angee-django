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

from typing import cast

from django.apps import apps
from django.conf import settings
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models
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
    PartyHandleManager,
    PartyManager,
)


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
        )

    def __str__(self) -> str:
        """Return the party's display name for Django displays."""

        return self.display_name


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

    objects = AngeeManager()

    class Meta:
        """Django model options for the person child model."""

        abstract = True
        rebac_resource_type = "parties/person"
        rebac_id_attr = "sqid"


class Organization(AngeeModel):
    """An organisation party — carries its legal name and primary domain."""

    runtime = True
    extends = "parties.Party"

    legal_name = models.TextField(blank=True, default="")
    domain = models.CharField(max_length=255, blank=True, default="")

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
        WHATSAPP = "whatsapp", "WhatsApp"
        YOUTUBE = "youtube", "YouTube"
        FACEBOOK = "facebook", "Facebook"
        OTHER = "other", "Other"

    sqid = SqidField(real_field_name="id", prefix="hdl_", min_length=8)
    platform = StateField(choices_enum=Platform, default=Platform.EMAIL)
    value = models.CharField(max_length=512)
    external_id = models.CharField(max_length=512, blank=True, default="")
    display_name = models.CharField(max_length=4096, blank=True, default="")
    label = models.CharField(max_length=64, blank=True, default="")
    is_preferred = models.BooleanField(default=False)
    is_own = models.BooleanField(default=False, db_index=True)
    is_verified = models.BooleanField(default=False)
    metadata = models.JSONField(blank=True, default=dict)
    party = models.ForeignKey(
        "parties.Party",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="handles",
    )

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


class PartyHandle(SqidMixin, AuditMixin, AngeeModel):
    """A confidence-bearing link between a party and one of its handles.

    A handle may carry several scored candidate parties, so a sync can surface an
    uncertain match as a weak link (a conflicting claim is recorded at ``0.3``
    confidence) instead of silently reassigning. The resolved owner is the
    highest-confidence, non-dismissed link — the value the manager materialises
    onto :attr:`Handle.party`.
    """

    runtime = True

    class Source(models.TextChoices):
        """Where a party↔handle link came from."""

        MANUAL = "manual", "Manual"
        IMPORT = "import", "Import"
        EMAIL_MATCH = "email_match", "Email Match"
        LLM = "llm", "LLM"
        OAUTH = "oauth", "OAuth"
        CARDDAV = "carddav", "CardDAV"

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
    confidence = models.FloatField(
        default=1.0,
        validators=(MinValueValidator(0.0), MaxValueValidator(1.0)),
    )
    source = StateField(choices_enum=Source, default=Source.MANUAL)
    is_confirmed = models.BooleanField(default=False)
    is_dismissed = models.BooleanField(default=False)
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

        Confirmation is the strongest signal the resolution ordering knows
        (``-is_confirmed`` sorts before any score), so the link also takes full
        confidence and the ``manual`` source — a later sync must not out-score a
        human decision. The actor must hold ``write`` on the link; the resolution
        cascade (the handle's owner pointer, both parties' counts) is server-owned
        bookkeeping and runs elevated.
        """

        if not self.has_access("write"):
            raise PermissionDenied("write access to the party-handle link is required")
        with system_context(reason="parties.party_handle.confirm"):
            self.confidence = 1.0
            self.source = self.Source.MANUAL  # type: ignore[assignment]  # TextChoices member unmodeled without django-stubs
            self.is_confirmed = True
            self.is_dismissed = False
            self.save(update_fields=["confidence", "source", "is_confirmed", "is_dismissed", "updated_at"])
            type(self).objects.resolve(self.handle)

    def dismiss(self) -> None:
        """Dismiss this link — the durable anti-link — then re-resolve the handle.

        A dismissed link survives as a row so the same match is never re-proposed
        (suggesters key on ``(party, handle)`` and skip existing links); resolution
        simply ignores it, demoting the handle to its next candidate or to unowned.
        Gated and elevated like :meth:`confirm`.
        """

        if not self.has_access("write"):
            raise PermissionDenied("write access to the party-handle link is required")
        with system_context(reason="parties.party_handle.dismiss"):
            self.is_dismissed = True
            self.is_confirmed = False
            self.save(update_fields=["is_dismissed", "is_confirmed", "updated_at"])
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


class Affiliation(SqidMixin, AuditMixin, AngeeModel):
    """A party's membership of an organisation (the vCard ``ORG``/``TITLE``/``ROLE``).

    The organisation is an organisation-kind :class:`Party` when known, falling
    back to a free-text ``organization_name`` when the organisation is not itself
    a tracked party.
    """

    runtime = True

    sqid = SqidField(real_field_name="id", prefix="afl_", min_length=8)
    party = models.ForeignKey(
        "parties.Party",
        on_delete=models.CASCADE,
        related_name="affiliations",
    )
    organization = models.ForeignKey(
        "parties.Party",
        null=True,
        blank=True,
        on_delete=models.SET_NULL,
        related_name="members",
    )
    organization_name = models.TextField(blank=True, default="")
    role = models.TextField(blank=True, default="")
    title = models.TextField(blank=True, default="")
    department = models.TextField(blank=True, default="")
    started_at = models.DateField(null=True, blank=True)
    ended_at = models.DateField(null=True, blank=True)
    is_primary = models.BooleanField(default=False)

    class Meta:
        """Django model options for the affiliation source model."""

        abstract = True
        ordering = ("-is_primary", "organization_name", "sqid")
        rebac_resource_type = "parties/affiliation"
        rebac_id_attr = "sqid"

    def __str__(self) -> str:
        """Return the affiliation title/org for Django displays."""

        return self.organization_name or self.title or str(self.organization_id)


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


class CircleMember(SqidMixin, AuditMixin, AngeeModel):
    """A party's membership of one circle, scored like a :class:`PartyHandle` link.

    Membership is confidence-bearing so a suggester (community detection, an
    org-domain rule, an LLM) can propose a weak membership for review instead of
    silently filing people; a human decision writes ``manual`` at full confidence.
    One row per ``(circle, party)`` — re-suggesting an existing membership updates
    the row rather than duplicating it.
    """

    runtime = True
    sqid_prefix = "cme_"

    class MembershipSource(models.TextChoices):
        """Where a circle membership came from.

        Named distinctly from :class:`PartyHandle.Source` — enum class names
        project as global GraphQL enum names, so they must be schema-unique.
        """

        MANUAL = "manual", "Manual"
        IMPORT = "import", "Import"
        LLM = "llm", "LLM"
        COMMUNITY = "community", "Community"
        RULE = "rule", "Rule"

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
    confidence = models.FloatField(
        default=1.0,
        validators=(MinValueValidator(0.0), MaxValueValidator(1.0)),
    )
    source = StateField(choices_enum=MembershipSource, default=MembershipSource.MANUAL)

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

    slug = models.SlugField(unique=True)
    name = models.CharField(max_length=128)
    """Anchor-side label: what the counterparty is *to the anchor* ("Mother")."""

    inverse_name = models.CharField(max_length=128, blank=True, default="")
    """Counterparty-side label ("Child"); blank means the kind is symmetric."""

    category = StateField(choices_enum=RelationshipCategory, default=RelationshipCategory.OTHER)

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


class Relationship(SqidMixin, AuditMixin, AngeeModel):
    """A typed edge from one party's viewpoint: the *other* is ``kind`` of ``party``.

    ``kind.name`` names what the counterparty is to the anchor ("Mother",
    "Mentor", "Colleague"); the counterparty's own card renders the reverse
    through :meth:`RelationshipKind.label_for` ("Child", "Mentee"). A single row
    carries both readings (Monica's Chandler shape — the mirror-row scheme was
    abandoned there for drifting). The counterparty is a tracked :class:`Party`
    when known, falling back to free-text ``other_name`` — the
    :class:`Affiliation` idiom — so a family-history relative who is not a
    directory entry still records. Edges are time-bounded (party-model
    ``from``/``thru``), so "was my colleague 2019–2022" stays queryable after it
    ends; an open edge has no ``ended_at``.
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
    other_name = models.CharField(max_length=256, blank=True, default="")
    """Free-text counterparty when the relative is not a tracked party."""

    kind = models.ForeignKey(
        "parties.RelationshipKind",
        on_delete=models.PROTECT,
        related_name="relationships",
    )
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
        )

    def __str__(self) -> str:
        """Return a readable edge description for Django displays."""

        return f"{self.party_id}←{self.kind_id}: {self.other_name or self.other_party_id}"


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
                party_model.objects.ingest_contact(parsed, folder=folder, owner_id=self.owner_id)
                seen.add(parsed.uid)
                resolved += 1
            party_model.objects.purge_missing(folder=folder, keep_uids=seen)
            folder.ctag = book.ctag
            folder.sync_token = book.sync_token
            folder.save(update_fields=["ctag", "sync_token", "updated_at"])
        return resolved


class CompanyParties(AngeeModel):
    """Public-face link from an ``iam.Company`` of record to a ``parties.Party``.

    A same-row ``extends`` merge (canon: ``iam_integrate_oidc`` over
    ``integrate.OAuthClient``): the composer folds ``party`` onto the single
    ``iam.Company`` table. ``iam`` stays fiscal- and party-free (it is the lowest
    addon in the dependency order); the of-record company borrows a ``Party`` for
    its public name/addresses/logo — invoice headers — when one is needed.
    ``parties`` already depends on ``iam``, so the dependency stays one-way.
    """

    extends = "iam.Company"

    party = models.OneToOneField(
        "parties.Party",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="company_of_record",
    )

    class Meta:
        """Abstract extension base merged into ``iam.Company``."""

        abstract = True
