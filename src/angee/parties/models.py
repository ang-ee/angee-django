"""Source models for the parties addon.

Parties are the people and organisations a project keeps track of. A party is
reached through one or more :class:`Handle` rows (an email address, a phone
number, a social handle) — the same handle that messaging uses as a sender or recipient,
so this addon is the contacts foundation the messaging addon builds on. The link
between a party and a handle is a confidence-bearing member statement
so a sync can record an uncertain match as a weak candidate instead of guessing.

``Party`` is a multi-table-inheritance parent; the concrete kind is the child
model (:class:`Person`, :class:`Organization`), not a column — a person carries
name parts and a link to its :class:`~angee.iam.models.User`, an organisation
carries its legal name and domain.

Parties are organised two ways, both human-owned facts: :class:`Circle` is a
private, overlapping grouping (a party may belong to many circles; circles nest
as a :class:`~angee.base.models.hierarchy.HierarchyMixin` tree), and :class:`Relationship`
is a typed, directed party↔party edge whose vocabulary
(:class:`RelationshipKind`) is catalogue data seeded from the XFN / vCard
``RELATED`` values — never a schema fact.
"""

from __future__ import annotations

import unicodedata
from collections.abc import Collection, Iterable, Mapping, Sequence
from dataclasses import asdict, replace
from datetime import datetime
from typing import Any, ClassVar, cast

from django.apps import apps
from django.conf import settings
from django.core.exceptions import ValidationError
from django.core.validators import validate_email
from django.db import models, transaction
from django_compose.models import Composable
from phonenumbers import (
    NumberParseException,
    PhoneNumberFormat,
    format_number,
    is_possible_number,
    is_valid_number,
    parse,
)
from rebac import (
    ObjectRef,
    PermissionDenied,
    SubjectRef,
    actor_context,
    backend,
    system_context,
    to_object_ref,
    to_subject_ref,
)
from rebac.mixins import RebacModelBase

from angee.base.fields.implementation import ImplClassField
from angee.base.fields.state import StateField
from angee.base.fields.text import ColorField
from angee.base.lineage import concrete_child, concrete_child_models, most_derived_concrete
from angee.base.models.audit import AuditMixin
from angee.base.models.base import AngeeDataModel
from angee.base.models.hierarchy import HierarchyMixin
from angee.base.models.identity import public_id_of
from angee.base.models.locks import ImmutableFieldsMixin
from angee.base.models.merge import MergeableMixin, merge_maintenance
from angee.base.models.ownership import OwnerMixin
from angee.base.models.roles import role_anchor
from angee.base.models.trash import TrashMixin
from angee.base.permissions import actor_user_id, instance_actor, reader_queryset
from angee.base.prefetch import prefetched
from angee.iam.roles import subject_has_role
from angee.integrate.mixins import LinkedRecordMixin, StatedRecordMixin
from angee.integrate.models import Bridge, IntegrationCreateMode
from angee.parties.backends import DirectoryBackend
from angee.parties.connections import ConnectionHandle
from angee.parties.fields import CountryCodeField, LanguageCodesField
from angee.parties.managers import (
    VISIBLE_CIRCLE_MEMBERS,
    CircleManager,
    DirectoryManager,
    FolderManager,
    HandleManager,
    PartyManager,
    PartyStatementQuerySet,
    RelationshipManager,
    ValueOwned,
)
from angee.parties.mixins import MembershipDecisionMixin, PartyAspectMixin, PartyStatementMixin
from angee.provenance.context import current_changeset
from angee.provenance.contrib.angee.history import HistoryMixin, ModelHistory
from angee.provenance.contrib.angee.managers import AngeeHistoryMergeableUnscopedManager
from angee.provenance.statements import (
    Aspect,
    PairAspect,
    SourceKind,
    StatementRecords,
    StatementsMixin,
    StatementSubjectManager,
)
from angee.resources.mixins import ResourceLoadMixin
from angee.tags.models import TaggedModel

_HYPHENS_AS_SPACES = str.maketrans(dict.fromkeys("-\u2010\u2011", " "))
"""Hyphens a name may carry, read as the space they separate words with (``Party.comparable_name``)."""


class Party(
    MergeableMixin,
    TrashMixin,
    LinkedRecordMixin,
    StatementsMixin,
    HistoryMixin,
    TaggedModel,
    OwnerMixin,
    AngeeDataModel,
    concrete=True,
):
    """A person or organisation the project tracks.

    The parent owns the common contact identity — the public id, ownership, the
    display name, avatar, notes and tags. Ownership is the transferable ``owner``
    (:class:`~angee.base.models.ownership.OwnerMixin`), which an insert defaults to the
    creator; ``created_by`` stays attribution. RecordLink owns directory payloads.
    The concrete kind (and its kind-specific fields) lives on the :class:`Person` /
    :class:`Organization` child row, which inherits the owner; a child's tags key
    on its party.
    """

    _angee_base = AngeeHistoryMergeableUnscopedManager()

    rebac_grantable = {"reader": "share", "identity_reader": "share"}

    owned_relations = ("addresses", "relationships")

    sqid_prefix = "pty_"
    history = ModelHistory(inherit=True, excluded_fields=("handle_count",))
    REVIEW_CONFIDENCE: ClassVar[float] = 0.5
    """The confidence at which a normal handle statement applies; below it, a statement awaits review.

    ``parties/handle#stating_directory`` in permissions.zed repeats it: a test holds the two to one number.
    """
    CLAIM_CONFIDENCE: ClassVar[float] = 0.4
    """The confidence of a source's claim for review on an address someone else holds: below review, never applied."""

    def handle_candidates(self, candidates: Sequence[Any]) -> Sequence[Any]:
        return [row for row in candidates if row.confidence >= self.REVIEW_CONFIDENCE]

    def apply_handle(self, *, member: Mapping[str, Any], statement: Any = None, **kwargs: Any) -> None:
        """Resolve the handle; a person's stance written now that wins it here needs authority over its address.

        Every route that writes a handle statement for a party ends here, in its
        resolution: a confirmation (the review queue's accept, an identity
        review's), taking control of an address (``HandleManager.claim_control``,
        which states a claim for review instead where this would refuse) and a
        restored confirmation (a history revert). When the stance this
        party's handle applies is a person's, written by the operation now
        running, its author must be able to make this party the holder of the
        address as it is held before the resolution
        (``PartyManager.holding_blocker``); if the handle then resolves to this
        party without that, the whole write is refused. The handle's rows are
        already locked: every statement write locks its subject's handles first
        (:meth:`lock_subject`). A merge's own maintenance moves stances, never
        states them, and is admitted (``merge_maintenance``).
        """

        handles = apps.get_model("parties", "Handle")
        refusal = None
        written = current_changeset()
        if (
            statement is not None
            and statement.source_kind == SourceKind.PERSON
            and statement.snak == statement.StatementSnak.VALUE
            and written is not None
            and statement.changeset_id == written.pk
            and not merge_maintenance()
        ):
            refusal = apps.get_model("parties", "Party").objects.holding_blocker(
                self, handles._base_manager.get(pk=member["handle"]), author=SubjectRef.parse(statement.asserted_by)
            )
        # The member carries the handle's key; resolution locks and reloads that row itself.
        handle = handles(pk=member["handle"])
        handle.resolve()
        if refusal is not None and handle.party_id == self.pk:
            raise PermissionDenied(refusal)

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
                # The reverse name lets a handle reach the directories asserting it (parties/handle). It is
                # the statement model's own name (``partystatement``), as Django names a subject's reverse,
                # so every statement arm of the parties schema spells its path the same way.
                member={
                    "handle": models.ForeignKey("parties.Handle", on_delete=models.PROTECT, related_name="%(class)s")
                },
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
    MERGED_PERMISSION = "merge_away"
    """A merge retires the party merged away to the trash: its write, but only a contact admin's of the roles."""
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
        "anniversary",
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

        owner = apps.get_model("parties", "Party").objects
        members = members or {}
        handle = members.get("handle")
        handle_ids = (
            (handle.pk if isinstance(handle, models.Model) else handle,)
            if handle is not None
            else owner.handle_statements().filter(subject_id=self.pk).values_list("handle_id", flat=True)
        )
        owner.lock_identity_rows(party_ids=(self.pk,), handle_ids=handle_ids)
        return super().lock_subject(members)

    @classmethod
    def lock_subjects(cls, subjects: Sequence[Any]) -> None:
        """Lock every one of these parties' Handles, then the parties and statements, in one identity pass.

        A party's Handles are the ones it has a statement on and the ones it holds
        (a holder written onto its row has no statement). Locking party after party
        would take a later party's Handles after an earlier party's row, the reverse
        of the Handle, Party order resolution and the identity verbs keep, so a pair
        (both endpoints) locks here.
        """

        cls._lock_identities({row.pk for row in subjects})

    @classmethod
    def lock_merge_records(cls, records: Sequence[Any]) -> None:
        """Lock the merge's parties and their same-person partners in one identity pass, before any party row.

        The fold re-keys each merged party's other pairs onto the survivor
        (``StatementPairManager.merge``), which locks the partner's Handles; taken
        then, after party rows, they would invert the Handle, Party order, so the
        partners join this pass, read again under its lock in case a pair committed
        meanwhile. One top-up closes the set: once the merge's parties are locked
        no new pair of theirs can start. What it takes late (a partner whose pair,
        or a Handle a party gained, committed while the first pass waited) is
        locked after these parties' rows; a deadlock there rolls the merge back,
        which the batch merge reports as that set's ``stopped``. That is the known
        limit of one pass.
        """

        ids = {row.pk for row in records}
        wanted = ids | cls._same_as_partners(ids)
        cls._lock_identities(wanted)
        # Under the lock no new pair of these parties can start (``for_pair`` locks both endpoints first),
        # but one committed while this pass waited may be missing: read again and top the set up.
        if missing := cls._same_as_partners(ids) - wanted:
            cls._lock_identities(wanted | missing)

    @classmethod
    def _same_as_partners(cls, ids: set[Any]) -> set[Any]:
        pairs = cls.same_as.model._base_manager.filter(models.Q(left_id__in=ids) | models.Q(right_id__in=ids))
        return {pk for pair in pairs.untrashed().values_list("left_id", "right_id") for pk in pair}

    @staticmethod
    def _lock_identities(party_ids: set[Any]) -> None:
        owner = apps.get_model("parties", "Party").objects
        ids = sorted(party_ids)
        with system_context(reason="parties.party.lock_identities"):
            stated = set(owner.handle_statements().filter(subject_id__in=ids).values_list("handle_id", flat=True))
        held = apps.get_model("parties", "Handle")._base_manager.filter(party_id__in=ids).values_list("pk", flat=True)
        owner.lock_identity_rows(party_ids=ids, handle_ids={*stated, *held})

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
        (:meth:`Handle.resolve`).
        """

        name = (self.display_name or "").strip()
        if not name or name == self.PLACEHOLDER_NAME:
            return False
        return any(character.isalpha() for character in name)

    def names_an_address(self, handles: Iterable[Any]) -> bool:
        """Whether ``display_name`` is one of these addresses, read as each one's platform normalizes it.

        A party a source named after its e-mail address carries no name of its
        own: two such records bear equal names because their addresses are equal.
        """

        handle_model = apps.get_model("parties", "Handle")
        return any(
            handle_model.normalize_value(row.platform, self.display_name) == row.normalized_value for row in handles
        )

    @property
    def comparable_name(self) -> str:
        """``display_name`` as two names compare for being one person's: case, accents and hyphens aside.

        "Zoë Example-Smith" and "zoe example smith" compare equal; whitespace collapses.
        """

        decomposed = unicodedata.normalize("NFKD", self.display_name or "")
        letters = "".join(character for character in decomposed if not unicodedata.combining(character))
        return " ".join(letters.translate(_HYPHENS_AS_SPACES).split()).casefold()

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

    @property
    def merge_common_scalar_fields(self) -> tuple[str, ...]:
        """Scalar merge choices the parent party projection carries for every subtype."""

        return self._merge_scalar_fields

    @property
    def merge_scalar_fields(self) -> tuple[str, ...]:
        """Scalar merge choices this party's native subtype admits; a person adds its name parts and dates."""

        return self.merge_scalar_fields_of(self.concrete_kind)

    @classmethod
    def merge_scalar_fields_of(cls, kind: str | None) -> tuple[str, ...]:
        """The scalar merge choices of a party of ``kind`` (``"person"`` adds its name parts and dates)."""

        if kind == "person":
            return cls._merge_scalar_fields + cls._person_merge_scalar_fields
        return cls._merge_scalar_fields

    def merge_values(self, merged: Sequence[Any]) -> dict[str, Any]:
        """The merge choices that keep every value: ``merged``'s value of each merge scalar this survivor leaves empty.

        Where both records carry a value, the survivor's stands. Passed as
        ``field_overrides``, each is kept and recorded as the merging person's choice
        (:meth:`keep_merge_fields`). Read without writing.
        """

        survivor = most_derived_concrete(self)
        values: dict[str, Any] = {}
        for row in merged:
            other = most_derived_concrete(row)
            for name in sorted(set(self.merge_scalar_fields).intersection(row.merge_scalar_fields)):
                if values.get(name, getattr(survivor, name)) in (None, "") and getattr(other, name) not in (None, ""):
                    values[name] = getattr(other, name)
        return values

    def keep_merge_fields(
        self,
        source: Party,
        field_overrides: Any,
        *,
        fills: Mapping[str, Any] | None = None,
        confirmed: Collection[str] | None = None,
    ) -> None:
        """Keep this survivor's merge scalars against ``source``: each override, else each fill, else its own value.

        Both endpoints must admit an override; a person's name parts and dates are
        admitted only between people. The fold then moves ``source``'s statements
        here, where survivorship could select their values, so the merging person
        confirms the kept values of every aspect an override or a differing value
        decides: a preferred statement precedes each moved one, at this resolve and
        every later one. Aspects both records agree on keep resolving over their
        sources; an override that no person-stated aspect holds saves as an
        ordinary edit. ``confirmed`` narrows what is recorded as the person's choice
        to those fields and the overrides (a batch merge confirms only what the
        person saw); a ``fills`` value outside them (``merge_values``) is written as
        a plain value, no one's statement, so a source that states the field may
        still replace it. ``None`` confirms every decided field (the Merge page).
        """

        overrides = {} if field_overrides is None else field_overrides
        if not isinstance(overrides, Mapping):
            raise ValidationError({"field_overrides": "Expected an object of field values."})
        filled = {name: value for name, value in (fills or {}).items() if name not in overrides}
        allowed = set(self.merge_scalar_fields).intersection(source.merge_scalar_fields)
        if unknown := (set(overrides) | set(filled)) - allowed:
            names = ", ".join(sorted(str(name) for name in unknown))
            raise ValidationError({"field_overrides": f"Unsupported merge field(s): {names}."})
        survivor = most_derived_concrete(self).with_actor(instance_actor(self))
        other = most_derived_concrete(source)
        kept = {}
        for name in sorted(allowed):
            field = survivor._meta.get_field(name)
            assert isinstance(field, models.Field)
            chosen = overrides if name in overrides else filled if name in filled else None
            kept[name] = field.clean(chosen[name], survivor) if chosen is not None else getattr(survivor, name)
        decided = {
            name for name, value in kept.items() if name in overrides or name in filled or value != getattr(other, name)
        }
        pinned = set(decided) if confirmed is None else (decided & set(confirmed)) | set(overrides)
        for manager in survivor.statement_managers():
            for aspect, declaration in manager.model.statement_aspects.items():
                if declaration.members or not declaration.admits_person or pinned.isdisjoint(declaration.fields):
                    continue
                values = {name: kept[name] if name in kept else getattr(survivor, name) for name in declaration.fields}
                survivor.confirm_statement(aspect=aspect, values=values)
                decided.difference_update(declaration.fields)
                pinned.difference_update(declaration.fields)
        edits = [name for name in sorted(pinned.intersection(overrides)) if getattr(survivor, name) != kept[name]]
        for name in edits:
            setattr(survivor, name, kept[name])
        if edits:
            survivor.save(update_fields=[*edits, "updated_at"])
        plain = {
            name: kept[name]
            for name in sorted(decided - pinned)
            if name in filled and getattr(survivor, name) != kept[name]
        }
        if plain:
            # No one's statement: a plain write, which survivorship keeps until a source states the field.
            type(survivor)._base_manager.filter(pk=survivor.pk).update(**plain)
            for name, value in plain.items():
                setattr(survivor, name, value)

    def apply_identity(
        self,
        *,
        expected_facts_hash: str = "",
        proposed: Mapping[str, Any] | None = None,
        choices: Mapping[str, Any] | None = None,
    ) -> tuple[str, dict[str, Any]]:
        """Apply plain reviewed identity values while the locked identity basis still matches.

        ``expected_facts_hash`` is :attr:`IdentityBasis.facts_hash` from review; it
        is compared with the complete basis locked here, and exact-replace
        expectations come from that same basis. Every write stays authorized as
        this row's actor. A replay after a successful change returns conflict
        because the retained basis hash no longer matches; unchanged choices are
        idempotent no-ops.
        """

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
        parties = type(self).objects
        with transaction.atomic(), actor_context(actor):
            basis = parties.identity_basis(public_id_of(self), actor=actor, lock=True)
            party, current = basis.party, basis.complete
            party.require_access("write")
            if basis.facts_hash != expected_facts_hash:
                return "conflict", {}
            results = {"name_result": "kept", "address_result": "kept", "handle_result": "kept"}
            if choices["name_action"] == "replace":
                results["name_result"] = party.replace_name_exact(
                    expected=current["name"], proposed=str(proposed["name"]), actor=actor,
                )
            addresses = apps.get_model("parties", "Address").objects
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
                link = parties.handle_statements().for_reader(actor).from_public_id(proposed["handle"]["statement_id"])
                if link is None or link.subject_id != party.pk:
                    raise ValidationError({"handle_action": "The proposed statement changed during review."})
                getattr(link.with_actor(actor), choices["handle_action"])()
                results["handle_result"] = f"{choices['handle_action']}ed"
            outcome = "applied" if any(value not in {"kept", "matched"} for value in results.values()) else "unchanged"
            return outcome, results

    def replace_name_exact(self, *, expected: str, proposed: str, actor: Any) -> str:
        """Apply a reviewed name only while this party's frozen name still matches."""

        normalized = " ".join(proposed.split()).strip()
        if not normalized:
            raise ValidationError({"name": "A replacement Party name must not be empty."})
        with transaction.atomic(), actor_context(actor):
            locked = type(self).objects.with_actor(actor).locked_get(pk=self.pk)
            if locked.display_name != expected:
                raise ValidationError({"name": "The Party name changed during review."})
            if not locked.has_access("write"):
                raise PermissionDenied("write access to the Party is required")
            if locked.display_name == normalized:
                return "matched"
            locked.display_name = normalized
            locked.save(update_fields=["display_name", "updated_at"])
            return "replaced"

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

    def recount_handles(self) -> None:
        """Refresh this party's derived handle count without inventing history."""

        count = apps.get_model("parties", "Handle").objects.filter(party=self).count()
        if self.handle_count != count:
            self.handle_count = count
            self.save_without_historical_record(update_fields=("handle_count",))

    def circle_names(self) -> list[str]:
        """Return this party's actor-visible circle names, from its read's prefetch when present.

        Resource reads install :meth:`PartyQuerySet.with_circle_names`; any other read
        prefetches the same readable memberships for this row as its actor.
        """

        def hint() -> models.Prefetch:
            return reader_queryset(type(self).objects.all(), self.actor()).circle_members_prefetch()

        memberships = prefetched(self, VISIBLE_CIRCLE_MEMBERS, hint)
        return list(dict.fromkeys(membership.circle.name for membership in memberships))

    def apply_pair(self, *, aspect: str, other: Any, statement: Any | None) -> None:
        """Identity survivorship selects a pending decision; merge chooses the survivor."""

        if aspect != "same_as":
            super().apply_pair(aspect=aspect, other=other, statement=statement)

    def backdate_creation(self, at: datetime | None) -> bool:
        """Move this party's ``created_at`` back to ``at`` when ``at`` is earlier; return whether it moved.

        A party's creation time is the earliest any of its provenance states: its
        insert, or the time a source gave the record it was minted from
        (``CreatedAtField``). It moves it afterwards, only earlier, never later
        and never past now, through the one rule
        (:meth:`PartyManager.backdate_creation`): the contact ingest calls it when
        a source with an earlier time lands on an existing party, and a merge when
        the survivor folds in an older record (:meth:`merge_fields`), so a survivor
        a person chose takes the earliest time of what it folds in. A tracked
        update compared in SQL: history records the move, and a party already as
        old writes nothing, no version and no live event. The stamp only ever
        moves earlier, so a time no earlier than the one this instance holds
        cannot move it, and is answered without a query.

        So a batch merge's "oldest" (``PartyManager.merge_duplicates``) is the
        source-oldest record. No reader of a party's ``created_at`` means "arrived
        here since" (``CreatedAtField`` names what was searched).
        """

        if at is None or (self.created_at is not None and at >= self.created_at):
            return False
        moved = cast("PartyManager[Party]", apps.get_model("parties", "Party").objects).backdate_creation(self.pk, at)
        if moved is None:
            return False
        self.created_at = moved
        return True

    def validate_merge(self, merged: Sequence[Any]) -> None:
        """Refuse a pair a person kept apart, and records of different kinds (a person and an organization)."""

        super().validate_merge(merged)
        kind = self.concrete_kind
        for row in merged:
            if row.concrete_kind != kind:
                raise ValidationError("Only people, or only organizations, merge together.")
            if self.same_as.kept_separate(self, row):
                raise ValidationError("These parties have been marked to stay separate.")
        # The survivor takes every merged record's links: never two live contacts of one pull-only directory.
        if refusal := apps.get_model("parties", "Party").objects.directory_merge_blocker(self, merged):
            raise ValidationError(refusal)

    def merge_fields(
        self,
        merged: Sequence[Any],
        *,
        field_overrides: Any = None,
        fill_empty: bool = False,
        confirmed_fields: Collection[str] | None = None,
        **options: Any,
    ) -> None:
        """Keep the survivor's merge scalars against each merged record and confirm each pair as the actor's.

        ``fill_empty`` keeps a merged record's value of each field this survivor
        leaves empty (:meth:`merge_values`, read here, under the merge's locks);
        ``confirmed_fields`` names the fields the merging person saw, the only ones
        recorded as their choice beside the overrides (:meth:`keep_merge_fields`).
        The survivor takes the earliest creation time of the records it folds in
        (:meth:`backdate_creation`), read from the locked rows.
        """

        super().merge_fields(merged, **options)
        self.backdate_creation(min((row.created_at for row in merged), default=None))
        fills = self.merge_values(merged) if fill_empty else {}
        for row in merged:
            self.keep_merge_fields(row, field_overrides, fills=fills, confirmed=confirmed_fields)
            pair = self.same_as.for_pair(self, row).with_actor(instance_actor(self))
            pair.confirm_statement(aspect="same_as", values={})
            # The merge decides the pair, so a directory's proposal of it is moot.
            apps.get_model("parties", "Party").objects.withdraw_directory_match(self, row)


_PERSON_FACT_PRECEDENCE = cast(
    tuple[SourceKind, ...], (SourceKind.PERSON, SourceKind.SYSTEM_OF_RECORD, SourceKind.DIRECTORY, SourceKind.IMPORT)
)
"""Who wins a person's own facts: the person, then a system of record, a directory, an import."""


class Person(Composable, metaclass=RebacModelBase, inherits="parties.Party"):
    """A human party — carries name parts, generic facts and an optional platform-user link.

    ``languages`` and ``occupation`` each have a statement group of their own, so a
    source stating one never states the other empty and a person editing one holds
    only that one. No neutral contact carries them (:attr:`MAPPED_ASPECTS`): a
    source states them through a directory mapping.
    """

    statements = StatementRecords(
        link="integrate.RecordLink",
        aspects={
            "name": Aspect(
                fields=("name_prefix", "given_name", "additional_name", "family_name", "name_suffix", "nickname"),
                precedence=_PERSON_FACT_PRECEDENCE,
            ),
            "dates": Aspect(fields=("birthday", "anniversary"), precedence=_PERSON_FACT_PRECEDENCE),
            "languages": Aspect(fields=("languages",), precedence=_PERSON_FACT_PRECEDENCE),
            "occupation": Aspect(fields=("occupation",), precedence=_PERSON_FACT_PRECEDENCE),
        },
        sqid_prefix="pns_",
    )
    MAPPED_ASPECTS: ClassVar[frozenset[str]] = frozenset({"languages", "occupation"})
    """The person groups parties declares that no neutral contact carries; a source states them through a mapping.

    The contact ingest states every other group parties declares on a person
    (:meth:`contact_aspects`), and a ``DirectoryMapping`` may declare these.
    """

    name_prefix = models.TextField(blank=True, default="")
    given_name = models.TextField(blank=True, default="")
    additional_name = models.TextField(blank=True, default="")
    family_name = models.TextField(blank=True, default="")
    name_suffix = models.TextField(blank=True, default="")
    nickname = models.TextField(blank=True, default="")
    birthday = models.DateField(null=True, blank=True)
    anniversary = models.DateField(null=True, blank=True)
    languages = LanguageCodesField(default=list, blank=True)
    """The languages the person uses with us, most preferred first; two with no order known are still two."""
    occupation = models.TextField(blank=True, default="")
    """What the person does, in their words or a source's; an employment's title stays on its relationship."""
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

    @classmethod
    def contact_aspects(cls) -> frozenset[str]:
        """Return the statement groups ``PartyManager.ingest_contact`` states through a contact's link.

        Those parties itself declares on a person, its party's and its own, but
        :attr:`MAPPED_ASPECTS`, read from parties' declarations rather than the
        composed tables, so a consumer donor's groups are never among them.
        """

        # On parties' abstract source models the attribute is still the declaration: StatementRecords keeps it there.
        declarations = (cast(StatementRecords, Party.statements), cast(StatementRecords, Person.statements))
        return frozenset(aspect for declaration in declarations for aspect in declaration.aspects) - cls.MAPPED_ASPECTS


class Organization(Composable, metaclass=RebacModelBase, inherits="parties.Party"):
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


class Handle(
    ResourceLoadMixin, MergeableMixin, HistoryMixin, TaggedModel, ImmutableFieldsMixin, AuditMixin, AngeeDataModel,
    concrete=True,
):
    """A reachable address or handle of a party on one platform.

    Keyed on ``(platform, value)`` and, when present, ``(platform, external_id)``
    — those unique constraints are the ingestion-dedup keys that make re-sync
    idempotent — and a live email handle on its spelling key
    (:meth:`spelling_key_of`), so any letter case or domain spelling of one
    address is one live handle. The keys are written once (:attr:`immutable_fields`): a message
    reads its addresses from its handles when it is sent, so a field write of one
    would send messages already written somewhere else, and no save of one is
    admitted, whoever makes it. Only the handle owner's verbs change a key:
    ``HandleManager.upsert`` when a source's ``external_id`` matched and the
    address behind it moved, and ``HandleManager.reidentify``. ``party`` is the
    resolved identity the Handle manager materialises; it is null until a handle
    is linked, so a handle synced for an unknown sender is still a valid row. A
    handle carries tags of its own, apart from its party's (a consumer marks a
    company's accounts-payable address).
    """

    @classmethod
    def after_resource_load(cls, instances: Iterable[Any], *, tier: str, source: str) -> None:
        """A seeded row's holder is stated, and one the seed moved or cleared loses its earlier statement.

        ``HandleManager.state_stored_holders`` takes each loaded row's holder as the
        seed's word, so resolution keeps it.
        """

        instances = tuple(instances)
        cast(HandleManager, cls._default_manager).state_stored_holders(instances)
        super().after_resource_load(instances, tier=tier, source=source)

    _angee_base = AngeeHistoryMergeableUnscopedManager()

    rebac_grantable = {"reader": "share"}
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
    immutable_fields = ("platform", "value", "external_id")
    platform = StateField(choices_enum=Platform, default=Platform.EMAIL)
    value = models.CharField(max_length=512)
    normalized_value = models.CharField(max_length=512, db_index=True, editable=False)
    """The person-matching key (:meth:`normalize_value`): what a lookup or a suggestion of a person compares."""
    normalized_version = models.PositiveSmallIntegerField(default=0, db_index=True, editable=False)
    """The :attr:`NORMALIZATION_VERSION` the rule had when the keys were computed; 0 before the rule had one.

    Written beside the keys by every write of them (:meth:`save`), never alone.
    """
    spelling_key = models.CharField(max_length=512, blank=True, default="", db_default="", editable=False)
    """What a live email handle is unique by (:meth:`spelling_key_of`), written beside ``normalized_value``.

    ``""`` on every other platform, where ``value`` itself is the address, and on a row stored before the rule had
    it, until the key repair (``HandleManager.renormalize_handles``) reaches it.
    """
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
    """The holder: the subject of the winning applied handle statement, written only by :meth:`resolve`.

    A source states the link (``PartyManager.assert_handle``); ``HandleManager.upsert``
    refuses a holder. A holder written onto the row with no statement behind it
    (a resource row, an older write) is stated by the ``stored_holder`` rule
    (``HandleManager.state_stored_holders``) after a resource load and by the
    ``resolve_stale_owners`` repair that provisioning runs, except a confirmed one
    or one another party's applied statement contests, which the repair leaves for
    a person, and a trashed one, left until a restore; until then a resolution of
    the handle clears it.
    """
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

    Written only by :meth:`Handle.set_avatar`, onto storage's image drive every signed-in user reads.
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
            # A merged-away row keeps its survivor's key as a redirect; a row the key repair has not reached has none.
            models.UniqueConstraint(
                fields=("spelling_key",),
                condition=models.Q(platform="email", merged_into__isnull=True) & ~models.Q(spelling_key=""),
                name="uq_handle_spelling_key",
            ),
        )
        indexes = (
            # A spelling of an address merged away lands on its survivor: the lookup reads merged rows by key too.
            models.Index(fields=("spelling_key",), condition=models.Q(platform="email"), name="ix_handle_spelling_key"),
        )

    def __str__(self) -> str:
        """Return the handle value for Django displays."""

        return self.value

    ACCOUNT_PLATFORMS: ClassVar[tuple[str, ...]] = ("email", "phone")
    """Platforms whose value is itself an account's address: an email address, a phone number."""

    NORMALIZATION_VERSION: ClassVar[int] = 2
    """The version of the normalization rule; bump it whenever the rule returns something else.

    The rule is :meth:`spelling_key_of`, :meth:`normalize_value` and
    :meth:`normalize_email_domain`. ``spelling_key`` and ``normalized_value`` are
    stored, so a changed rule leaves stored keys behind. Each row carries the
    version its keys were computed under (:attr:`normalized_version`), and
    provisioning saves again the rows whose version is not this one
    (``HandleManager.renormalize_handles``). A test pins the rule's outputs beside
    the number, so changing an output without a bump fails.
    """

    @classmethod
    def identifies_account(cls, platform: str, external_id: str = "") -> bool:
        """Whether a handle names one account: an email address, a phone number or a platform id.

        A value on any other platform without a platform id is only a name (a
        friend an export records by name), which many accounts share.
        :meth:`identifies_account_filter` is the same rule over stored rows.
        """

        return bool(external_id) or platform in cls.ACCOUNT_PLATFORMS

    @classmethod
    def identifies_account_filter(cls, prefix: str = "") -> models.Q:
        """The handle rows :meth:`identifies_account` admits, through the relation path ``prefix``."""

        return models.Q(**{f"{prefix}platform__in": cls.ACCOUNT_PLATFORMS}) | ~models.Q(**{f"{prefix}external_id": ""})

    @classmethod
    def spellings_filter(cls, values: Iterable[str]) -> models.Q:
        """The email handles spelling any of these addresses: each one's row by the value as given, and every row,
        live or merged away, owning its spelling key (:meth:`spelling_key_of`).

        The email arm of ``HandleManager.find_identities``, which decides which
        row an address lands on; at most one live row owns a key
        (``uq_handle_spelling_key``). One arm naming the platform, so the
        ``(platform, value)`` key's index and the spelling key's serve it.
        """

        spelled = sorted(set(values))
        keys = sorted({key for value in spelled if (key := cls.spelling_key_of(cls.Platform.EMAIL, value))})
        return models.Q(platform=cls.Platform.EMAIL) & (models.Q(value__in=spelled) | models.Q(spelling_key__in=keys))

    @classmethod
    def identified_connection(cls, parsed: ConnectionHandle) -> bool:
        """Whether a connection names its account by a real identifier, not by a display name only.

        :meth:`identifies_account` decides, unless the adapter overrides that
        inference with its typed confidence.
        """

        if parsed.confidence is not None:
            return parsed.confidence >= 1.0
        return cls.identifies_account(parsed.platform, parsed.external_id)

    @classmethod
    def spelling_key_of(cls, platform: str, value: str) -> str:
        """Return what makes two spellings one email handle: the address trimmed and lower-cased, its domain read by
        :meth:`normalize_email_domain`; ``""`` on every other platform, where ``value`` itself is the address, and for
        a value with no ``@``, which is no address and so has no other spelling.

        ``lower()``, not ``casefold()``: ``Straße@`` and ``STRASSE@`` stay two
        addresses. A provider's alias (a Gmail dot or plus-tag) is an address of
        its own, which only the person-matching key (:meth:`normalize_value`)
        groups. A live email handle is unique by it (``uq_handle_spelling_key``).
        The key is stored: changing what this returns means bumping
        :attr:`NORMALIZATION_VERSION`.
        """

        if platform != cls.Platform.EMAIL:
            return ""
        local, at, domain = (value or "").strip().lower().rpartition("@")
        return f"{local}@{cls.normalize_email_domain(domain)}" if at else ""

    @classmethod
    def normalize_value(cls, platform: str, value: str) -> str:
        """Return the person-matching key for one platform/value pair: what groups the handles of probably one person.

        Every platform strips surrounding whitespace and lowercases. An email
        address starts from its spelling key (:meth:`spelling_key_of`, its domain
        in one spelling) and additionally collapses dots and plus-tags in Gmail
        local parts for both ``gmail.com`` and ``googlemail.com`` domains, so a
        provider's aliases match one person while each stays a handle of its own.
        Phone and
        WhatsApp values parse without an assumed region and format as E.164; values
        require a leading country code and must be possible and valid. Anything
        unparseable, invalid, or region-unknown falls back to its digits so
        punctuation still does not fork the same contact point. iMessage carries
        either kind of address, so it routes by value shape — the email rule when
        the value contains ``@``, the phone rule otherwise. It is read where a
        person is looked up or suggested; which handle an address lands on is the
        spelling key's, but for two sites still open for a ruling, where it picks
        the handle a provider's alias lands on: a directory adoption's
        through-handle (``PartyManager._land_handles``) and a new claimant's first
        email (``PartyManager.claimant_as_system``).

        The key is stored: changing what this returns for any value means
        bumping :attr:`NORMALIZATION_VERSION`, so provisioning saves the stored
        keys again.
        """

        normalized = (value or "").strip().lower()
        if platform in (cls.Platform.EMAIL, cls.Platform.IMESSAGE) and "@" in normalized:
            local, _, domain = cls.spelling_key_of(cls.Platform.EMAIL, normalized).rpartition("@")
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

    @classmethod
    def is_fallback_key(cls, platform: str, key: str) -> bool:
        """Whether a person-matching key is the phone rule's fallback, a value's bare digits, not a number it read.

        The phone rule (phone, WhatsApp, an iMessage value with no ``@``) gives an
        E.164 number, which starts with ``+``; a value it cannot read as a valid
        number falls back to its digits, which distinct numbers share
        (``1-800-FLOWERS`` and ``1-800-COLLECT`` are both ``1800``). Such a key
        still groups handles for a suggestion, and names no one address, so a
        person's merge never takes it for another spelling (:meth:`validate_merge`).
        """

        phone_rule = platform in (cls.Platform.PHONE, cls.Platform.WHATSAPP) or (
            platform == cls.Platform.IMESSAGE and "@" not in key
        )
        return phone_rule and not key.startswith("+")

    @staticmethod
    def normalize_email_domain(domain: str) -> str:
        """Return the one spelling an email domain compares by: lower-cased, each label as the IDNA codec reads it.

        The one rule for a domain, whoever compares one: a handle's spelling key
        (:meth:`spelling_key_of`) and person-matching key (:meth:`normalize_value`)
        and a domain stored as typed elsewhere, such as ``Organization.domain``,
        read through it on both sides. Each label makes the standard library's
        ``idna`` round trip (IDNA 2003), the codec Django's ``punycode`` writes a
        domain with to send it: a punycode label reads as Unicode, and Unicode
        spellings IDNA 2003 sends as one domain are one (NFD and NFC, full-width
        letters, ``ß`` and ``ss``). A label the codec cannot read or write, such as
        ``xn--strae-oqa`` (IDNA 2008's ``straße``, a domain of its own), stays as
        typed.

        Handle keys are stored from it: changing what this returns means bumping
        :attr:`NORMALIZATION_VERSION`.
        """

        labels = []
        for label in (domain or "").strip().lower().split("."):
            try:
                label = label.encode("idna").decode("idna")
            except UnicodeError:
                pass
            labels.append(label)
        return ".".join(labels)

    @staticmethod
    def email_value(address: str) -> str:
        """Return an email address trimmed and lower-cased: the form compose, a channel's sender and inbound mail still
        pass when they land an address.

        Which handle an address lands on is the spelling key's
        (:meth:`spelling_key_of`); this form still decides the spelling a handle
        those writers create first stores as its ``value``, and the keys of
        ``Message.reply_addresses``. It is removed with its callers.
        """

        return (address or "").strip().lower()

    # Django hard-codes it in django/core/validators.py, EmailValidator.__call__ (len(value) > 320): RFC 3696 section 3.
    EMAIL_ADDRESS_LIMIT: ClassVar[int] = 320
    """The longest an email address may be (RFC 3696 section 3): the bound Django's ``EmailValidator`` applies."""

    @classmethod
    def is_email_address(cls, value: str) -> bool:
        """Whether ``value`` can be an email address at all, read as :meth:`normalize_value` reads it.

        What cannot be an address is no address; anything else is one, however
        unusual. Surrounding whitespace aside, the value has exactly one ``@``,
        a non-empty name part in any script (``jörg@``, ``peña@``) with no
        whitespace, control or format character (Unicode categories ``Cc`` and
        ``Cf``: a zero-width space, a direction mark), and a domain that passes
        the domain rule of Django's ``EmailValidator``, which reads an
        internationalised domain as typed or in its punycode form. A value
        longer than an address may be (:attr:`EMAIL_ADDRESS_LIMIT`) or than the
        ``value`` column holds is none. It says nothing of whether the mailbox
        exists.
        """

        column = cls._meta.get_field("value")
        assert isinstance(column, models.Field) and column.max_length is not None
        address = (value or "").strip()
        if not address or len(address) > min(cls.EMAIL_ADDRESS_LIMIT, column.max_length) or address.count("@") != 1:
            return False
        name, domain = address.split("@")
        unseen = any(character.isspace() or unicodedata.category(character) in ("Cc", "Cf") for character in name)
        if not name or unseen:
            return False
        return domain in validate_email.domain_allowlist or bool(validate_email.validate_domain_part(domain))

    @staticmethod
    def normalize_display_name(value: str) -> str:
        """Return the display-name pooling key: what cross-platform display-name pooling compares."""

        return " ".join((value or "").split()).casefold()

    def save(self, *args: Any, **kwargs: Any) -> None:
        """Persist the handle with its keys in lockstep, and never as a second live handle of an email address.

        The keys (``spelling_key``, ``normalized_value``) are computed on an
        insert, on a full save, and whenever a partial save names ``platform``,
        ``value`` or a key column; the key columns are then written together with
        ``normalized_version``, which always says which rule they came from.
        First, an insert, a keyed save, or a save naming ``merged_into`` (a revert
        restoring a merged-away row, keyed or not) that would keep this row a live
        email handle beside the one owning its address's spelling key
        (:meth:`value_owner`) raises :class:`ValueOwned` on ``value``: nothing is
        written, the instance's keys are left as they were, a write-once allowance
        granted for this save is spent as a save spends it, and the caller's
        transaction stays usable. ``uq_handle_spelling_key`` is the backstop for
        what that read cannot see (a concurrent insert, ``bulk_create``).
        """

        update_fields = kwargs.get("update_fields")
        key_fields = ("spelling_key", "normalized_value", "normalized_version")
        keyed = update_fields is None or {"platform", "value", *key_fields}.intersection(update_fields)
        if (self._state.adding or keyed or "merged_into" in (update_fields or ())) and (
            owner := self.value_owner()
        ) is not None:
            # The foundation spends an allowance in its own save (ImmutableFieldsMixin), which this refusal precedes.
            # Debt at angee.base: the mixin offers no public way to spend an allowance on a refused save; this line
            # switches to it when the mixin has one.
            self._allowed_immutable_fields = set()
            raise ValueOwned({"value": ValidationError(
                "Handle %(owner)s is this address; a second live handle of it is not kept.",
                code="value_owned",
                params={"owner": public_id_of(owner)},
            )})
        if self._state.adding or keyed:
            self.spelling_key = self.spelling_key_of(self.platform, self.value)
            self.normalized_value = self.normalize_value(self.platform, self.value)
            self.normalized_version = self.NORMALIZATION_VERSION
            if update_fields is not None:
                kwargs["update_fields"] = [*update_fields, *(name for name in key_fields if name not in update_fields)]
        super().save(*args, **kwargs)

    def value_owner(self) -> Handle | None:
        """Return the other live email handle owning the spelling key of this row's address, or ``None``.

        The key is this row's own, computed from ``value``
        (:meth:`spelling_key_of`), never read from its stored column, which the
        key repair may not have reached; the other row is matched by its stored
        key under ``uq_handle_spelling_key``'s own condition, so at most one
        answers. It reads nothing off email, for a value that is no address, or
        for a merged-away row (a redirect keeps its survivor's key). A structural
        read through the base manager: no REBAC scope, no audit row.
        :meth:`save` refuses to keep this row live beside it (:class:`ValueOwned`).
        """

        key = self.spelling_key_of(self.platform, self.value)
        if not key or self.merged_into_id is not None:
            return None
        live = type(self)._base_manager.filter(platform=self.Platform.EMAIL, spelling_key=key, merged_into__isnull=True)
        return live.exclude(pk=self.pk).order_by("pk").first()

    def is_spelling_of(self, value: str) -> bool:
        """Whether ``value`` is this handle's address: its own ``value``, or on email another letter case or domain
        spelling of it (:meth:`spelling_key_of`, computed from ``value``, never the stored column)."""

        key = self.spelling_key_of(self.platform, value)
        return value == self.value or bool(key) and key == self.spelling_key_of(self.platform, self.value)

    def validate_merge(self, merged: Sequence[Any]) -> None:
        """A contact alias keeps its platform and the directory identity lock order; a person merges an alias.

        Every later message to a merged handle's address goes to the kept one. So a
        person's merge (:meth:`~angee.base.models.merge.MergeableMixin.merge`, which
        binds the kept handle to its actor) admits only another spelling or alias of
        the kept address, the same person-matching key, unless that actor is a
        contact administrator: an effective member of ``parties/role:contact_admin``,
        as platform administrators are. The key is computed from each row's value
        (:meth:`normalize_value`), never read from the stored column, which the key
        repair may not have reached, and a phone rule's fallback (a value's bare
        digits, :meth:`is_fallback_key`) names no one address, so it is never another
        spelling. Anyone else merges the contacts, which says two addresses reach one
        person and moves no address. System work (``fold``, as
        ``HandleManager.reidentify`` folds an ``@lid`` into its number) merges any two
        handles on one platform.
        """

        super().validate_merge(merged)
        if any(row.platform != self.platform for row in merged):
            raise ValidationError("Only handles on the same platform can be merged.")
        actor, elevated = self.effective_actor()
        key = self.normalize_value(self.platform, self.value)
        spelled = not self.is_fallback_key(self.platform, key) and all(
            self.normalize_value(row.platform, row.value) == key for row in merged
        )
        if (
            not elevated
            and not spelled
            and not subject_has_role(actor, ObjectRef("parties/role", "contact_admin"))
        ):
            raise ValidationError(
                "Only a contact administrator merges two different addresses; merge their contacts instead."
            )
        apps.get_model("parties", "Party").objects.lock_identity_rows(
            party_ids=(), handle_ids=(self.pk, *(row.pk for row in merged)),
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
            self.save(update_fields=(*values, "updated_at"))
        for row in merged:
            if self.avatar_id is None and row.avatar_id is not None:
                self.set_avatar(row.avatar)

    def retire_merged(self, merged: Any) -> None:
        """Redirect the merged handle, then recompute both handles' parties after the shared reference moves.

        A person's merge writes the redirect as that person (``MergeableMixin.merge``),
        and a contact administrator writes a handle only through the party it reaches.
        The moves have taken the merged handle's statements to the kept one, so its
        resolution clears that party: redirecting first keeps the write the merge
        authorized. The redirect's history version still names the former party;
        the resolution that follows records the cleared one, and carries each
        handle's resolved party onto its instance.
        """

        super().retire_merged(merged)
        merged.resolve()
        self.resolve()

    def resolve(self) -> None:
        """Materialize the applied, eligible cross-party winner and both parties' handle counts.

        The handle, its competing parties and their statements are locked in the
        identity order first; this instance then carries the resolved party.
        Because it relocks and reloads the handle under that lock, a caller holding
        only the key may call it on a transient ``Handle(pk=...)``. A row verb that
        reads its row without relocking it needs the row loaded through its manager.
        """

        party_model = apps.get_model("parties", "Party")
        with transaction.atomic(), system_context(reason="parties.handle.resolve"):
            handles, parties = party_model.objects.lock_identity_rows(party_ids=(), handle_ids=(self.pk,))
            handle = handles[self.pk]
            candidates = list(
                party_model.objects.handle_statements().applied()
                .filter(handle=handle, subject__is_trashed=False)
                .select_related("subject", "link__stream")
            )
            winner = min(candidates, key=lambda row: row.sort_key(), default=None)
            previous = handle.party_id
            resolved = winner.subject_id if winner else None
            confirmed = bool(winner and winner.is_confirmed)
            if handle.party_id != resolved or handle.party_link_confirmed != confirmed:
                handle.party_id, handle.party_link_confirmed = resolved, confirmed
                handle.save(update_fields=("party", "party_link_confirmed", "updated_at"))
            for pk in sorted({value for value in (previous, resolved) if value is not None}):
                parties[pk].recount_handles()
            handle._party_links_resolved()
        self.party_id, self.party_link_confirmed = handle.party_id, handle.party_link_confirmed

    def set_avatar(self, avatar: Any | None) -> bool:
        """Set the avatar this handle's own network shows for it; return whether it changed.

        The one write path of :attr:`avatar`: a source reports the avatar
        (:meth:`HandleManager.fill_avatar`), never writes the column itself. ``avatar``
        is a stored image (``File.objects.ingest_image``), or ``None`` when the network
        shows none.
        """

        avatar_id = avatar.pk if avatar is not None else None
        with system_context(reason="parties.handle.set_avatar"), transaction.atomic():
            locked = type(self).objects.filter(pk=self.pk).lock_if_supported().first()
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

    def land_addresses(
        self,
        party: Any,
        addresses: Iterable[Any],
        *,
        asserted_by: Any,
        link: Any,
        created_by_id: Any,
        replaces: Sequence[Any] = (),
    ) -> None:
        """State the addresses ``link`` names for ``party``; withdraw the party's others it or ``replaces`` stated.

        Each parsed address takes its stored form (:meth:`as_stored`) and matches a
        row of the party by its identity (:meth:`identity_key`), repeated equal
        addresses included, so a respelled address updates its row and keeps what
        the source does not carry; an unmatched one is created. ``replaces`` are
        the links of records a merge at the source folded into ``link``'s. Only
        those links' statements are withdrawn; rows are never deleted.
        """

        stored = list(self.model._base_manager.filter(party_id=party.pk).order_by("pk"))
        landed: list[AddressT] = []
        for parsed in addresses:
            values = asdict(self.as_stored(parsed))
            key = self.identity_key(values)
            address = next(
                (
                    row
                    for row in stored
                    if row not in landed
                    and self.identity_key({name: getattr(row, name) for name in self.components}) == key
                ),
                None,
            )
            if address is None:
                address = self.create(party_id=party.pk, created_by_id=created_by_id, **values)
            address.statements.set(aspect="address", values=values, asserted_by=asserted_by, link=link)
            landed.append(address)
        statements = self.model.statements
        for stating in (link, *replaces):
            removed = statements.filter(link=stating, subject__party_id=party.pk).exclude(
                subject_id__in=[row.pk for row in landed]
            )
            statements.withdraw(asserted_by, list(removed.values_list("subject_id", flat=True)), link=stating)

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
                created_by_id=actor_user_id(actor),
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


class Address(TrashMixin, PartyAspectMixin, HistoryMixin, AuditMixin, AngeeDataModel, concrete=True):
    """A physical or postal address of a party (the vCard ``ADR`` property).

    There is intentionally no ``(party, label)`` uniqueness — a party may carry
    two same-labelled addresses — so a CardDAV mapper keys idempotency on the
    address content, not the label.
    """

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


class Folder(StatedRecordMixin, HistoryMixin, OwnerMixin, AngeeDataModel, concrete=True):
    """A group of parties — the local mirror of a synced address book.

    The contacts counterpart of storage's ``Drive``/``Folder`` and knowledge's
    ``Vault`` container idea, kept to exactly what sync needs today: the directory
    it mirrors and its name. The address book's identity is the
    ``integrate.RecordLink`` of the directory's ``addressbooks`` stream that targets
    this folder; that link is the container of the address book's contacts stream.
    The source states the name through that link, so a person's rename outranks a
    remote one. Owned through its transferable ``owner``, which a sync's insert
    takes from the directory owner it stamps as ``created_by``. A folder no source
    has stated stays an ordinary row (``person_stances_once_stated``). Manual
    creation and a folder tree (``parent``) are deferred until a create path lands
    to exercise them.
    """

    sqid_prefix = "fol_"
    statements = StatementRecords(
        link="integrate.RecordLink",
        aspects={
            "identity": Aspect(
                fields=("name",),
                precedence=cast(tuple[SourceKind, ...], (SourceKind.PERSON, SourceKind.DIRECTORY)),
            ),
        },
        sqid_prefix="fos_",
    )
    name = models.CharField(max_length=200)
    directory = models.ForeignKey(
        "parties.Directory",
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name="folders",
    )

    objects = FolderManager()

    class Meta:
        """Django model options for the folder source model."""

        abstract = True
        ordering = ("name", "sqid")
        rebac_resource_type = "parties/folder"

    def __str__(self) -> str:
        """Return the folder name for Django displays."""

        return self.name


class Circle(HierarchyMixin, OwnerMixin, AngeeDataModel, concrete=True):
    """A private, overlapping grouping of parties — how the owner organises people.

    Circles are the curated counterpart of :class:`Folder` (which mirrors a synced
    address book): "Family", "Inner Circle", a climbing crew. A party may belong to
    many circles (:class:`CircleMember`), and circles nest as a tree — overlap
    comes from multi-membership, never from multiple parents. The tree is
    :class:`~angee.base.models.hierarchy.HierarchyMixin`'s materialized path, so "everyone in
    this circle including sub-circles" is an indexed prefix scan
    (``Circle.objects.subtree_of(circle)``), and ``hierarchy_scope_fields`` keeps a
    personal circle tree from straddling owners: the transferable ``owner`` scopes
    the tree, so a transfer never leaves a circle's parent or children under
    another owner.

    Circles are an organising surface only — they never gate visibility or
    sharing; REBAC stays the one authorization owner.
    """

    def member_count(self) -> int:
        """Return the count selected by CircleQuerySet.with_member_counts, or zero."""

        return int(self.__dict__.get("_member_count", 0))

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


class CircleMember(MembershipDecisionMixin, AuditMixin, HistoryMixin, AngeeDataModel, concrete=True):
    """A party's reviewable membership of one circle.

    Membership is a plain decision row so a suggester (community detection, an
    org-domain rule, an LLM) can propose a weak membership for review instead of
    silently filing people; a human decision records acceptance at full confidence.
    One row per ``(circle, party)`` — re-suggesting an existing membership updates
    the row rather than duplicating it.
    """

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


class Directory(Bridge, inherits="integrate.Integration"):
    """A connected contacts source that syncs parties from an external directory.

    An ``integrate.Integration`` child (so it draws its credential / owner / status
    from the connection substrate) and a ``Bridge`` (so the scheduler and the eager
    ``syncIntegration`` mutation drive it). ``backend_class`` selects the protocol
    (``carddav`` from ``parties_integrate_carddav``), and ``config`` carries what
    that backend needs to reach its source (CardDAV's server URL). The backend tests
    the connection (:meth:`test_connection`), refuses a credential it does not use
    (:meth:`credential_blocker`) and declares which way the contacts sync
    (``DirectoryBackend.direction``); every vendor connects through
    ``Directory.objects.connect`` (:class:`DirectoryManager`). The inherited
    ``Bridge.sync()`` drives the backend streams; the parties managers own the
    contact projection and ingest path.
    """

    integration_create_mode: ClassVar[IntegrationCreateMode | None] = IntegrationCreateMode.CONNECT
    rebac_grantable = {"contact_reader": "share"}

    objects = DirectoryManager()

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

        backend_class = self.get_backend_class_impl()
        return backend_class(self)

    def test_connection(self) -> str:
        """Exercise the selected backend's connection (the Integration test contract)."""

        return self.backend.test_connection()

    def credential_blocker(self, credential: Any) -> str | None:
        """Refuse a credential this directory's backend does not use (``DirectoryBackend.credential_blocker``).

        Integrate raises it on every attachment (``Integration.admit_credential``),
        so neither a connect, the console's credential patch nor a reconnect points
        a directory at a credential its backend would send elsewhere; the connect
        picker shows it beside each account.
        """

        return self.backend.credential_blocker(credential)

    def grant_owner_contact_read(self) -> bool:
        """Grant this directory's owner ``contact_reader``, the read of its contacts; return whether it was missing.

        The one writer of that grant, a decision made when the directory is
        connected: ``Directory.objects.connect`` calls it for the connecting
        owner, and the provision repair (``DirectoryManager.grant_owner_contact_reads``)
        for every existing directory. It is system work and idempotent: an owner
        who already reads the contacts (granted, or through a group) is granted
        nothing. A transfer does not move it: a person grants a later owner
        through the record-access surface, and the previous owner keeps the
        grant until someone removes it. A grant that follows ownership would be
        written by the transfer's owner, a ``transfer_ownership`` override on
        the directory calling this for the new owner and revoking the previous
        one in the transfer's transaction.
        """

        if self.owner_id is None:
            return False
        owner = to_subject_ref(self.owner)
        with system_context(reason="parties.directory.owner_contact_read"):
            if backend().check_access(subject=owner, action="contact_read", resource=to_object_ref(self)).allowed:
                return False
            self.system_grant_record_access("contact_reader", owner)
        return True


PartiesRole = role_anchor("parties/role")
"""The ``parties/role`` anchor: ``contact_reader``, ``contact_manager`` and ``contact_admin`` hold on every party
what a per-record ``reader``, ``editor`` and owner hold on one (``permissions.zed``). See
:func:`angee.base.models.roles.role_anchor`.
"""
