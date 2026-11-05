"""Native parties model owners for the contacts domain."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any, ClassVar, cast

from django.apps import apps
from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models, transaction
from rebac import PermissionDenied, actor_context, database_context
from rebac.database import write_alias

from angee.base.fields.relations import RetainedGenericRelation
from angee.base.lineage import concrete_child, concrete_child_models
from angee.base.models.identity import public_id_of
from angee.base.models.persistence import require_projection
from angee.iam.models.base import ProtectedDataModel, ProtectedModel
from angee.iam.models.merge import ProtectedMergeableMixin
from angee.iam.models.ownership import OwnerMixin
from angee.iam.models.trash import ProtectedTrashMixin
from angee.iam.permissions import instance_actor
from angee.parties.managers import (
    AddressManager,
    PartyManager,
    PartyStatementQuerySet,
)
from angee.parties.mixins import PartyStatementMixin
from angee.parties.models.relationships import RelationshipKind
from angee.provenance.contrib.angee.history import HistoryMixin, ModelHistory
from angee.provenance.contrib.angee.managers import AngeeHistoryMergeableUnscopedManager
from angee.provenance.statements import (
    Aspect,
    PairAspect,
    SourceKind,
    StatementRecords,
    StatementsMixin,
)
from angee.tags.models import TaggedModel


class Party(
    ProtectedMergeableMixin, ProtectedTrashMixin, StatementsMixin, HistoryMixin, TaggedModel, OwnerMixin,
    ProtectedDataModel, concrete=True
):
    """A person or organisation the project tracks.

    The parent owns the common contact identity — the public id, ownership, the
    display name, avatar, notes and tags. Ownership is the transferable ``owner``
    (:class:`~angee.iam.models.ownership.OwnerMixin`), which an insert defaults to the
    creator; ``created_by`` stays attribution. RecordLink owns directory payloads.
    The concrete kind (and its kind-specific fields) lives on the :class:`Person` /
    :class:`Organization` child row, which inherits the owner; a child's tags key
    on its party.
    """

    _angee_base = AngeeHistoryMergeableUnscopedManager()

    rebac_grantable = {"reader": "write"}

    sqid_prefix = "pty_"
    history = ModelHistory(inherit=True, excluded_fields=("handle_count",))
    REVIEW_CONFIDENCE: ClassVar[float] = 0.5

    def handle_candidates(self, candidates: Sequence[Any]) -> Sequence[Any]:
        return [row for row in candidates if row.confidence >= self.REVIEW_CONFIDENCE]

    def apply_handle(self, *, member: Mapping[str, Any], **kwargs: Any) -> None:
        apps.get_model("parties", "Handle").objects.db_manager(self._state.db).get(pk=member["handle"]).resolve()

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

        A structural fact projected from the multi-table-inheritance child rows
        that share this party's primary key — not a stored column (the child model
        *is* the kind). Standalone write paths use ``get_concrete_kind``.
        """

        if "_concrete_kind" in self.__dict__:
            value = self.__dict__["_concrete_kind"]
            return cast(RelationshipKind.PartyKind, value) if value is not None else None
        for kind in (RelationshipKind.PartyKind.ORGANIZATION, RelationshipKind.PartyKind.PERSON):
            child_model = kind.model()
            assert child_model is not None
            if isinstance(self, child_model):
                return kind
        require_projection(self, "_concrete_kind")
        return self.get_concrete_kind()

    @classmethod
    def concrete_kind_expression(cls) -> models.Case:
        """Read the two declared child-row kinds in the party selection."""

        choices = []
        for kind in (RelationshipKind.PartyKind.ORGANIZATION, RelationshipKind.PartyKind.PERSON):
            child_model = kind.model()
            assert child_model is not None
            choices.append(models.When(
                models.Exists(child_model._base_manager.filter(pk=models.OuterRef("pk"))),
                then=models.Value(str(kind)),
            ))
        return models.Case(*choices, default=models.Value(None), output_field=models.CharField())

    def get_concrete_kind(self) -> RelationshipKind.PartyKind | None:
        """Resolve one party's structural child kind explicitly for write paths."""

        for child_model in concrete_child_models(apps.get_model("parties", "Party")):
            if isinstance(self, child_model) or concrete_child(self, child_model) is not None:
                for kind in (RelationshipKind.PartyKind.ORGANIZATION, RelationshipKind.PartyKind.PERSON):
                    if RelationshipKind.PartyKind(kind).model() is child_model:
                        return cast(RelationshipKind.PartyKind, kind)
        return None

    @property
    def merge_common_scalar_fields(self) -> tuple[str, ...]:
        """Scalar merge choices present on the parent Party projection."""
        return self._merge_scalar_fields

    @property
    def merge_scalar_fields(self) -> tuple[str, ...]:
        """Scalar choices admitted when merging this party's native subtype."""
        if self.get_concrete_kind() == "person":
            return self.merge_common_scalar_fields + self._person_merge_scalar_fields
        return self.merge_common_scalar_fields

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

        both_people = self.get_concrete_kind() == "person" and source.get_concrete_kind() == "person"
        allowed = set(self.merge_scalar_fields).intersection(source.merge_scalar_fields)
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
        allowed = {
            "name_action": {"keep", "replace"},
            "address_action": {"keep", "add", "replace"},
            "handle_action": {"keep", "confirm", "dismiss"},
        }
        if set(choices) != set(allowed) or any(value not in allowed[name] for name, value in choices.items()):
            raise ValidationError({"choices": "Identity choices are invalid."})
        actor = instance_actor(self)
        manager = type(self).objects.db_manager(self._state.db, hints={"instance": self})
        using = manager.db
        with database_context(using), transaction.atomic(using=using), actor_context(actor):
            basis = manager.identity_basis(public_id_of(self), actor=actor, lock=True)
            party, current = basis.party, basis.complete
            party.require_access("write")
            if basis.facts_hash != expected_facts_hash:
                return "conflict", {}
            results = {"name_result": "kept", "address_result": "kept", "handle_result": "kept"}
            if choices["name_action"] == "replace":
                results["name_result"] = party.replace_name_exact(
                    expected=current["name"],
                    proposed=str(proposed["name"]),
                    actor=actor,
                )
            addresses = apps.get_model("parties", "Address").objects.db_manager(using)
            if choices["address_action"] == "add":
                results["address_result"], _ = addresses.attach_exact(
                    party=party,
                    values=proposed["address"],
                    actor=actor,
                    label=proposed["address"]["label"],
                    conflict="append",
                )
            elif choices["address_action"] == "replace":
                primary = next((row for row in current["addresses"] if row["is_primary"]), None)
                expected = (
                    addresses.system_context(reason="parties.party.apply_identity").from_public_id(primary["id"])
                    if primary
                    else None
                )
                results["address_result"], _ = addresses.replace_primary_exact(
                    party=party,
                    values=proposed["address"],
                    actor=actor,
                    expected_id=expected.pk if expected else None,
                    label=proposed["address"]["label"],
                )
            if choices["handle_action"] != "keep":
                link = manager.handle_statements().for_reader(actor).from_public_id(proposed["handle"]["statement_id"])
                if link is None or link.subject_id != party.pk:
                    raise ValidationError({"handle_action": "The proposed statement changed during review."})
                getattr(link.with_actor(actor), choices["handle_action"])()
                results["handle_result"] = f"{choices['handle_action']}ed"
            outcome = "applied" if any(value not in {"kept", "matched"} for value in results.values()) else "unchanged"
            return outcome, results

    def replace_name_exact(self, *, expected: str, proposed: str, actor: Any) -> str:
        """Apply a reviewed name only while the frozen Party name still matches."""

        normalized = " ".join(proposed.split()).strip()
        if not normalized:
            raise ValidationError({"name": "A replacement Party name must not be empty."})
        using = write_alias(type(self), using=self._state.db, instance=self)
        with database_context(using), transaction.atomic(using=using), actor_context(actor):
            locked = type(self).objects.db_manager(using).with_actor(actor).locked_get(pk=self.pk)
            if locked.display_name != expected:
                raise ValidationError({"name": "The Party name changed during review."})
            if not locked.has_access("write"):
                raise PermissionDenied("write access to the Party is required")
            if locked.display_name == normalized:
                return "matched"
            locked.display_name = normalized
            locked.save(update_fields=["display_name", "updated_at"])
            return "replaced"

    def visible_circle_names(self) -> list[str]:
        """Return one party's actor-visible circle names through the fast projection.

        Resource reads install :meth:`PartyQuerySet.with_circle_names`; alternate
        authored paths fall back to the same independently scoped membership and
        circle collections instead of silently presenting an empty list.
        """

        prefetched = getattr(self, "_angee_visible_circle_members", None)
        if prefetched is not None:
            return list(dict.fromkeys(membership.circle.name for membership in prefetched))

        circles = apps.get_model("parties", "Circle").objects.db_manager(self._state.db).all()
        if actor := instance_actor(self):
            circles = circles.with_actor(actor)
        return list(
            circles.memberships()
            .filter(party_id=self.pk)
            .order_by("circle__name")
            .values_list("circle__name", flat=True)
            .distinct()
        )

    def recount_handles(self) -> None:
        """Refresh this Party's derived handle count without inventing history."""

        owner = apps.get_model("parties", "Handle").objects.db_manager(
            self._state.db,
            hints={"instance": self},
        )
        count = owner.filter(party=self).count()
        if self.handle_count != count:
            self.handle_count = count
            self.save_without_historical_record(using=self._state.db, update_fields=("handle_count",))

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
            and any(row["id"] == link_id and (row["rank"], row["snak"]) != confirmed for row in current["handles"])
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


class Person(ProtectedModel, inherits="parties.Party"):
    """A human party — carries name parts and an optional platform-user link."""

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


class Organization(ProtectedModel, inherits="parties.Party"):
    """An organisation party — carries its legal name and primary domain."""

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
