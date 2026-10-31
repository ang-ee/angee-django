"""Native relationships model owners for the contacts domain."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, cast

from django.apps import apps
from django.core.exceptions import ValidationError
from django.db import models

from angee.base.fields.state import StateField
from angee.base.models.audit import AuditMixin
from angee.base.models.base import AngeeDataModel
from angee.base.models.trash import TrashMixin
from angee.parties.managers import (
    RelationshipManager,
)
from angee.parties.mixins import PartyAspectMixin
from angee.provenance.contrib.angee.history import HistoryMixin
from angee.provenance.statements import (
    Aspect,
    SourceKind,
    StatementRecords,
)
from angee.resources.mixins import ResourceLoadMixin

if TYPE_CHECKING:
    from angee.parties.models.parties import Party


class RelationshipKind(ResourceLoadMixin, AuditMixin, AngeeDataModel, concrete=True):
    """The relationship vocabulary — types as catalogue data, never schema.

    One row expresses both directions of an asymmetric type through
    ``inverse_name`` ("Parent" / "Child"); a blank inverse means the type is
    symmetric ("Friend"). The master tier seeds the XFN / vCard ``RELATED``
    vocabulary (adopted by slug, so a project may rename labels without forking
    rows), and users may add their own kinds alongside.
    """

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
    def employee(cls, *, using: str | None = None) -> RelationshipKind | None:
        """Return the employment vocabulary row when this installation provides it."""

        return cls._base_manager.using(using).filter(slug="employee").first()

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


class Relationship(TrashMixin, PartyAspectMixin, HistoryMixin, AuditMixin, AngeeDataModel, concrete=True):
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

    objects = RelationshipManager()
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
