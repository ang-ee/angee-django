"""The history read surface: a record's changesets, read through the record's own gate.

One timeline per record, keyed by changeset. Each entry carries the changeset's
attribution, the field deltas simple-history computes between the record's
versions, the statements the changeset asserted, withdrew or applied for the
record, and the survivor/absorbed facts of a merge. Authorization is the
record's ``read``; gated fields the actor cannot read are left out of every
delta and statement value, the same redaction the record's own projection
carries. Statement tables mirror history tables, so they take the same gate.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable
from datetime import datetime
from itertools import groupby
from typing import Any, cast

import strawberry
from django.apps import apps
from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import models
from rebac import SubjectRef, current_actor
from rebac.field_visibility import redacted_fields
from rebac.resources import model_for_subject_type, model_resource_type
from simple_history.utils import get_history_manager_for_model
from strawberry.scalars import JSON

from angee.actors import source_kind_for
from angee.autoconfig import SETTINGS
from angee.evidence import FactAuthority
from angee.graphql.actions import ActionResult, action_guard, authorized_permission_target
from angee.graphql.ids import PublicID, instance_for_id, require_instance_for_id
from angee.identity import public_id_for, public_id_of, public_subject_ref
from angee.models import (
    HistoryMixin,
    SourceKind,
    Statement,
    StatementsMixin,
    model_history,
    prime_readable_statement_records,
    record_display_label,
    record_history,
)
from angee.permissions import require_rebac_queryset
from angee.provenance.models import Changeset as ChangesetContract
from angee.refs import generic_pointer_model
from angee.serialization import json_safe

Changeset = apps.get_model(getattr(settings, "ANGEE_CHANGESET_MODEL", SETTINGS["ANGEE_CHANGESET_MODEL"]))

HISTORY_LIMIT_DEFAULT = 25
HISTORY_LIMIT_MAX = 100

# The nested choices keep their owner's names on the wire; a bare ``Action`` would collide.
strawberry.enum(cast(Any, ChangesetContract.Action), name="ChangesetAction")
strawberry.enum(cast(Any, ChangesetContract.Activity), name="ChangesetActivity")
strawberry.enum(cast(Any, ChangesetContract.Outcome), name="ChangesetOutcome")
strawberry.enum(cast(Any, SourceKind), name="StatementSourceKind")
strawberry.enum(cast(Any, Statement.StatementSnak), name="StatementSnak")
strawberry.enum(cast(Any, Statement.StatementRank), name="StatementRank")
strawberry.enum(cast(Any, Statement.StatementStatus), name="StatementStatus")
strawberry.enum(cast(Any, FactAuthority), name="FactAuthority")


class HistoryType(models.TextChoices):
    """Public names for django-simple-history's native row operators."""

    CREATED = "+", "Created"
    CHANGED = "~", "Changed"
    DELETED = "-", "Deleted"


strawberry.enum(cast(Any, HistoryType), name="HistoryType")


@strawberry.type
class HistoryRecordType:
    """A record a changeset touched; ``retained`` says whether the row still exists."""

    model: str
    id: PublicID
    retained: bool


@strawberry.type
class HistoryFieldChangeType:
    """One field's value before and after the changeset; references carry public ids."""

    field: str
    model: str
    id: PublicID
    old: JSON | None
    new: JSON | None


@strawberry.type
class HistoryReferenceType:
    """One readable statement source or evidence record, already page-batched."""

    model: str
    id: PublicID
    label: str
    pointer: str


@strawberry.type
class HistoryStatementType:
    """One statement version the changeset wrote for the record, as stored."""

    id: PublicID
    aspect: str
    asserted_by: str
    source_kind: SourceKind
    snak: Statement.StatementSnak
    rank: Statement.StatementRank
    status: Statement.StatementStatus
    applied: bool
    confidence: float
    history_type: HistoryType
    values: JSON
    source: HistoryReferenceType | None
    evidence: list[HistoryReferenceType]


@strawberry.type
class FieldProvenanceType:
    """The applied statement decorating one visible field on its subject form."""

    field: str
    authority: FactAuthority
    origin: str
    statement_model: str
    statement_id: PublicID


@strawberry.type
class HistoryMergeType:
    """The survivor and the records a merge changeset absorbed into it."""

    survivor: HistoryRecordType
    absorbed: list[HistoryRecordType]


@strawberry.interface
class HistoryAttribution:
    """Changeset attribution shared by compact and detailed record history."""

    id: PublicID
    actor: str | None
    actor_model: str | None
    actor_id: PublicID | None
    source_kind: SourceKind | None
    action: ChangesetContract.Action
    activity: ChangesetContract.Activity
    change_reason: str
    occurred_at: datetime
    recorded_at: datetime
    outcome: ChangesetContract.Outcome | None


@strawberry.type
class HistorySummaryType(HistoryAttribution):
    """One compact changeset line, without field or revert projection work."""

    @classmethod
    def from_changesets(cls, changesets: Iterable[Any]) -> list[HistorySummaryType]:
        """Project a page while resolving each distinct actor once."""

        page = list(changesets)
        actors = _actor_projections(page)
        return [cls(**_attribution(row, *actors.get(row.actor_ref, (None, None)))) for row in page]


@strawberry.type
class HistoryEntryType(HistoryAttribution):
    """One changeset with the record's authorized deltas and replay eligibility."""

    changes: list[HistoryFieldChangeType]
    statements: list[HistoryStatementType]
    merge: HistoryMergeType | None
    can_revert: bool


@strawberry.type
class RecordHistoryType:
    """A page of one record's changesets, newest first."""

    model: str
    id: PublicID
    total: int
    entries: list[HistoryEntryType]


def history_model(label: str) -> type[HistoryMixin]:
    """Return the tracked, permission-governed model ``label`` names."""

    try:
        model = apps.get_model(label)
    except (LookupError, ValueError) as error:
        raise ValidationError(f"Unknown model {label!r}.") from error
    if not issubclass(model, HistoryMixin) or record_history(model) is None:
        raise ValidationError(f"{model._meta.label} keeps no history.")
    return model


def provenance_model(label: str) -> type[models.Model]:
    """Return the resource model ``label`` names for the global form decoration."""

    try:
        model = apps.get_model(label)
    except (LookupError, ValueError) as error:
        raise ValidationError(f"Unknown model {label!r}.") from error
    if model_resource_type(model) is None:
        raise ValidationError(f"{model._meta.label} is not a permission-governed resource.")
    return model


def _statement_origin(statement: Statement) -> str:
    """Name a readable asserting principal, otherwise disclose only its kind."""

    source = statement.readable_asserting_source
    if source is not None:
        return str(public_subject_ref(source))
    return str(SourceKind(statement.source_kind).label)


def field_provenance(record: StatementsMixin, actor: Any) -> list[FieldProvenanceType]:
    """Project each visible field's applied statement in deterministic owner order."""

    hidden = redacted_fields(record)
    projected: list[FieldProvenanceType] = []
    for statement_model in type(record).statement_models():
        rows = statement_model.objects.for_reader(actor).applied().filter(subject_id=record.pk).order_by("aspect", "pk")
        for row in rows:
            declaration = statement_model.statement_aspects[row.aspect]
            projected.extend(
                FieldProvenanceType(
                    field=name,
                    authority=cast(Any, row.fact_authority),
                    origin=_statement_origin(row),
                    statement_model=statement_model._meta.label,
                    statement_id=PublicID(public_id_of(row)),
                )
                for name in declaration.fields
                if name not in hidden
            )
    return projected


_Version = tuple[type[models.Model], Any, Any, Any | None]


def _attribution(changeset: Any, actor: SubjectRef | None, source_kind: str | None) -> dict[str, Any]:
    """Project stored attribution through public actor identity."""

    public_actor = public_subject_ref(actor) if actor is not None else None
    resolved = model_for_subject_type(public_actor.subject_type) if public_actor is not None else None
    actor_model = resolved[0]._meta.label if resolved is not None else None
    actor_id = (
        public_actor.subject_id if public_actor is not None and public_actor.subject_id not in ("", "*") else None
    )
    return dict(
        id=PublicID(public_id_of(changeset)),
        actor=str(public_actor) if public_actor is not None else None,
        actor_model=actor_model,
        actor_id=PublicID(actor_id) if actor_id else None,
        source_kind=cast(Any, SourceKind(source_kind)) if source_kind is not None else None,
        action=cast(Any, ChangesetContract.Action(changeset.action)),
        activity=cast(Any, ChangesetContract.Activity(changeset.activity)),
        change_reason=changeset.reason_display,
        occurred_at=changeset.occurred_at,
        recorded_at=changeset.recorded_at,
        outcome=cast(Any, ChangesetContract.Outcome(changeset.outcome)) if changeset.outcome else None,
    )


def _actor_projections(changesets: Iterable[Any]) -> dict[str, tuple[SubjectRef, str | None]]:
    """Resolve each page actor's stored source kind once."""

    actors = {row.actor_ref: SubjectRef.parse(row.actor_ref) for row in changesets if row.actor_ref}
    return {value: (actor, source_kind_for(actor)) for value, actor in actors.items()}


def _reference_value(model_field: models.Field[Any, Any], value: Any) -> Any:
    """Expose primary-key references as public IDs; retain other stored values."""

    if value is None:
        return None
    if isinstance(model_field, models.ForeignKey) and model_field.target_field.primary_key:
        return public_id_for(cast(type[models.Model], model_field.related_model), value)
    return json_safe(value)


def _history_reference(record: models.Model, *, pointer: str = "", label: str | None = None) -> HistoryReferenceType:
    """Project an already authorized record without causing a second read."""

    model = generic_pointer_model(type(record))
    return HistoryReferenceType(
        model=model._meta.label,
        id=PublicID(public_id_of(record)),
        label=label or record_display_label(record),
        pointer=pointer,
    )


def _statement_source_reference(version: Any) -> HistoryReferenceType | None:
    """Prefer a readable retained link; otherwise name the readable principal."""

    link = getattr(version, "_readable_statement_link", None)
    if link is not None:
        stream = link.stream
        stream_name = f"{stream.key} ({stream.partition})" if stream.partition else stream.key
        label = f"{record_display_label(stream.integration)} · {stream_name} · {link.external_key}"
        return _history_reference(link, label=label)
    source = getattr(version, "_readable_asserting_source", None)
    return _history_reference(source) if source is not None else None


class RecordHistory:
    """Assemble a bounded history page from the record and history owners."""

    def __init__(self, record: HistoryMixin, actor: Any) -> None:
        self.record = record
        self.actor = actor
        self.model = type(record)
        self.using = record._state.db
        self.scopes = record.history_scope()
        self.statement_scopes: dict[type[Statement], models.Q] = {}
        for model, scope in record.history_scopes().items():
            if model not in self.scopes:
                assert issubclass(model, Statement)
                self.statement_scopes[model] = scope

    def changesets(self) -> models.QuerySet[Any]:
        """The record's changesets, newest first; ordering is the changeset owner's."""

        return Changeset.objects.sudo(reason="provenance.record_history").touching(self.record)

    def entries(self, changesets: Iterable[Any]) -> list[HistoryEntryType]:
        """Project an already bounded window, also used by the combined Comments feed."""

        page = list(changesets)
        ids = [changeset.pk for changeset in page]
        versions = self._versions(ids)
        statements = self._statement_versions(ids)
        self._prime_statement_references(statements)
        redacted = self._redactions(versions, statements)
        merges = self._merges(page)
        actors = _actor_projections(page)
        revertable = self.record.revertable_changesets(page, actor=self.actor)
        return [
            HistoryEntryType(
                **_attribution(changeset, *actors.get(changeset.actor_ref, (None, None))),
                changes=[
                    change for state in versions.get(changeset.pk, ()) for change in self._changes(state, redacted)
                ],
                statements=self._statements(statements.get(changeset.pk, ()), redacted),
                merge=merges.get(changeset.pk),
                can_revert=changeset.pk in revertable,
            )
            for changeset in page
        ]

    def page(self, limit: int, offset: int) -> RecordHistoryType:
        changesets = self.changesets()
        return RecordHistoryType(
            model=self.model._meta.label,
            id=PublicID(public_id_of(self.record)),
            total=changesets.count(),
            entries=self.entries(changesets[offset : offset + limit]),
        )

    def _versions(self, changeset_ids: list[Any]) -> dict[Any, list[_Version]]:
        """Fetch selected boundaries and their predecessors once per table."""

        grouped: defaultdict[Any, list[_Version]] = defaultdict(list)
        for table, predicate in self.scopes.items():
            key = table._meta.pk.attname
            historical = get_history_manager_for_model(table).model
            predecessor = (
                historical._base_manager.using(self.using)
                .filter(**{key: models.OuterRef(key)}, history_id__lt=models.OuterRef("history_id"))
                .order_by("-history_id")
                .values("history_id")[:1]
            )
            rows = (
                get_history_manager_for_model(table)
                .using(self.using)
                .filter(
                    predicate,
                    changeset_id__in=changeset_ids,
                )
                .annotate(_angee_predecessor_id=models.Subquery(predecessor))
                .order_by("changeset_id", key, "history_id")
            )
            boundaries = []
            predecessor_ids = set()
            for (changeset_id, _pk), versions in groupby(
                rows.iterator(),
                key=lambda row: (row.changeset_id, getattr(row, key)),
            ):
                boundary = list(versions)
                predecessor_id = boundary[0]._angee_predecessor_id
                boundaries.append((changeset_id, boundary[0], boundary[-1], predecessor_id))
                if predecessor_id is not None:
                    predecessor_ids.add(predecessor_id)
            predecessors = get_history_manager_for_model(table).using(self.using).in_bulk(predecessor_ids)
            for changeset_id, first, last, predecessor_id in boundaries:
                grouped[changeset_id].append((table, first, last, predecessors.get(predecessor_id)))
        return dict(grouped)

    def _statement_versions(
        self,
        changeset_ids: list[Any],
    ) -> dict[Any, list[tuple[type[Statement], Any]]]:
        """Fetch every selected statement model once for the bounded page."""

        grouped: defaultdict[Any, list[tuple[type[Statement], Any]]] = defaultdict(list)
        for statement_model, predicate in self.statement_scopes.items():
            versions = (
                statement_model.history.using(self.using)
                .filter(
                    predicate,
                    changeset_id__in=changeset_ids,
                )
                .order_by("history_id")
            )
            for version in versions:
                grouped[version.changeset_id].append((statement_model, version))
        return dict(grouped)

    def _prime_statement_references(
        self,
        statements: dict[Any, list[tuple[type[Statement], Any]]],
    ) -> None:
        """Batch readable principals, evidence and retained record links for this page."""

        versions = [version for rows in statements.values() for _statement_model, version in rows]
        if not versions:
            return
        prime_readable_statement_records(versions, self.actor)
        links_by_model: defaultdict[type[models.Model], set[Any]] = defaultdict(set)
        keyed_versions = []
        for statement_model, version in (row for rows in statements.values() for row in rows):
            field = statement_model.link_field()
            if field is None:
                continue
            if version.link_id is None or field.remote_field is None:
                continue
            link_model = field.remote_field.model
            if not isinstance(link_model, type):
                continue
            links_by_model[link_model].add(version.link_id)
            keyed_versions.append((version, link_model, version.link_id))
        readable_links: dict[tuple[type[models.Model], Any], models.Model] = {}
        for link_model, ids in links_by_model.items():
            rows = require_rebac_queryset(link_model).with_actor(self.actor).with_action("read").filter(pk__in=ids)
            if any(field.name == "stream" for field in link_model._meta.fields):
                rows = rows.select_related("stream__integration")
            readable_links.update({(link_model, row.pk): row for row in rows})
        for version, link_model, pk in keyed_versions:
            version._readable_statement_link = readable_links.get((link_model, pk))

    def _redactions(
        self,
        versions: dict[Any, list[_Version]],
        statements: dict[Any, list[tuple[type[Statement], Any]]],
    ) -> dict[tuple[type[models.Model], Any, Any | None], frozenset[str]]:
        """Resolve current subjects in bulk, delegating retained snapshots to their owner."""

        snapshots: defaultdict[type[models.Model], dict[tuple[Any, Any | None], Any | None]] = defaultdict(dict)
        for table, _first, last, _previous in (state for states in versions.values() for state in states):
            snapshots[table][getattr(last, table._meta.pk.attname), last.changeset_id] = last
        for statement_model, version in (row for rows in statements.values() for row in rows):
            snapshots[statement_model.statement_subject_model].setdefault((version.subject_id, None), None)

        result: dict[tuple[type[models.Model], Any, Any | None], frozenset[str]] = {}
        for model, items in snapshots.items():
            keys = {pk for pk, _changeset_id in items}
            readable = (
                require_rebac_queryset(model)
                .with_actor(self.actor)
                .with_action("read")
                .using(self.using)
                .filter(pk__in=keys)
                if self.actor is not None
                else model._base_manager.none()
            )
            current = {row.pk: row for row in readable}
            missing = keys - current.keys()
            retained = set(model._base_manager.using(self.using).filter(pk__in=missing).values_list("pk", flat=True))
            hidden = frozenset(field.name for field in model._meta.concrete_fields)
            history = model_history(model)
            decisions: defaultdict[Any, list[frozenset[str]]] = defaultdict(list)
            for (pk, changeset_id), snapshot in items.items():
                row = current.get(pk)
                fields = redacted_fields(row) if row is not None else None
                if fields is None:
                    fields = (
                        history.redacted(snapshot, self.actor, current=None, retained=pk in retained)
                        if history is not None and snapshot
                        else hidden
                    )
                result[model, pk, changeset_id] = fields
                decisions[pk].append(fields)
            for pk, redactions in decisions.items():
                result.setdefault((model, pk, None), frozenset().union(*redactions))
        return result

    def _changes(
        self,
        state: _Version,
        redactions: dict[tuple[type[models.Model], Any, Any | None], frozenset[str]],
    ) -> list[HistoryFieldChangeType]:
        """Project the history owner's native diff through its redaction decision."""

        table, first, last, previous = state
        pk = getattr(last, table._meta.pk.attname)
        history = model_history(table)
        if history is None:
            return []
        hidden = redactions.get(
            (table, pk, last.changeset_id),
            redactions[table, pk, None],
        )
        projected: list[HistoryFieldChangeType] = []
        for change in history.diff(first, last, previous=previous).changes:
            if change.field in hidden:
                continue
            model_field = table._meta.get_field(change.field)
            assert isinstance(model_field, models.Field)
            projected.append(
                HistoryFieldChangeType(
                    field=model_field.name,
                    model=table._meta.label,
                    id=PublicID(public_id_for(table, pk)),
                    old=_reference_value(model_field, change.old),
                    new=_reference_value(model_field, change.new),
                )
            )
        return projected

    def _statements(
        self,
        versions: Iterable[tuple[type[Statement], Any]],
        redactions: dict[tuple[type[models.Model], Any, Any | None], frozenset[str]],
    ) -> list[HistoryStatementType]:
        """Project prefetched statement versions, redacted through their subjects."""

        projected: list[HistoryStatementType] = []
        for statement_model, version in versions:
            subject_model = statement_model.statement_subject_model
            redacted = redactions.get(
                (subject_model, version.subject_id, version.changeset_id),
                redactions[subject_model, version.subject_id, None],
            )
            declaration = statement_model.statement_aspects[version.aspect]
            values = {}
            for name in (*declaration.fields, *declaration.members):
                if name in redacted:
                    continue
                field = statement_model._meta.get_field(name)
                assert isinstance(field, models.Field)
                values[name] = _reference_value(field, field.value_from_object(version))
            projected.append(
                HistoryStatementType(
                    id=PublicID(public_id_for(statement_model, version.id)),
                    aspect=version.aspect,
                    asserted_by=version.asserted_by,
                    source_kind=cast(Any, SourceKind(version.source_kind)),
                    snak=cast(Any, Statement.StatementSnak(version.snak)),
                    rank=cast(Any, Statement.StatementRank(version.rank)),
                    status=cast(Any, Statement.StatementStatus(version.status)),
                    applied=version.applied,
                    confidence=version.confidence,
                    history_type=cast(Any, HistoryType(version.history_type)),
                    values=cast(JSON, values),
                    source=_statement_source_reference(version),
                    evidence=[
                        _history_reference(record, pointer=ref.pointer)
                        for ref, record in getattr(version, "_readable_evidence_records", ())
                    ],
                )
            )
        return projected

    def _merges(self, changesets: Iterable[Any]) -> dict[Any, HistoryMergeType]:
        """Load all merge members and retained identities once for the page."""

        boundaries = {
            changeset.pk: (
                history_model(changeset.record_ref.model_label),
                changeset.record_ref.object_id,
            )
            for changeset in changesets
            if changeset.activity == ChangesetContract.Activity.MERGE and changeset.record_ref.model_label
        }
        absorbed: defaultdict[Any, set[Any]] = defaultdict(set)
        by_model: defaultdict[type[HistoryMixin], set[Any]] = defaultdict(set)
        for changeset_id, (model, _pk) in boundaries.items():
            by_model[model].add(changeset_id)
        for model, changeset_ids in by_model.items():
            raw_survivor = boundaries[next(iter(changeset_ids))][1]
            record = model(pk=model._meta.pk.to_python(raw_survivor))
            for table in record.history_tables():
                key = table._meta.pk.attname
                redirected = "merged_into" in {field.name for field in table._meta.fields}
                fields = ("changeset_id", key, "history_type", *(("merged_into_id",) if redirected else ()))
                rows = (
                    get_history_manager_for_model(table)
                    .using(self.using)
                    .filter(
                        changeset_id__in=changeset_ids,
                    )
                    .values_list(*fields)
                )
                for changeset_id, pk, history_type, *redirect in rows:
                    survivor = model._meta.pk.to_python(boundaries[changeset_id][1])
                    if pk != survivor and (history_type == HistoryType.DELETED or redirect and redirect[0] == survivor):
                        absorbed[changeset_id].add(pk)
        retained: set[tuple[type[models.Model], Any]] = set()
        for model, changeset_ids in by_model.items():
            keys = {pk for changeset_id in changeset_ids for pk in absorbed[changeset_id]}
            retained.update(
                (model, pk)
                for pk in model._base_manager.using(self.using)
                .filter(
                    pk__in=keys,
                )
                .values_list("pk", flat=True)
            )
        return {
            changeset_id: HistoryMergeType(
                survivor=HistoryRecordType(
                    model=model._meta.label,
                    id=PublicID(public_id_for(model, survivor)),
                    retained=True,
                ),
                absorbed=[
                    HistoryRecordType(
                        model=model._meta.label,
                        id=PublicID(public_id_for(model, pk)),
                        retained=(model, pk) in retained,
                    )
                    for pk in sorted(absorbed[changeset_id], key=str)
                ],
            )
            for changeset_id, (model, raw_survivor) in boundaries.items()
            for survivor in (model._meta.pk.to_python(raw_survivor),)
        }


def _validate_page(limit: int, offset: int) -> None:
    if not 1 <= limit <= HISTORY_LIMIT_MAX or offset < 0:
        raise ValidationError(f"History requires a limit from 1 to {HISTORY_LIMIT_MAX} and a nonnegative offset.")


@strawberry.type
class HistoryQuery:
    @strawberry.field
    def field_provenance(self, model: str, id: PublicID) -> list[FieldProvenanceType] | None:
        """Applied field statements through their subject and source read gates."""

        subject_model = provenance_model(model)
        actor = current_actor()
        queryset = (
            require_rebac_queryset(subject_model).with_actor(actor).with_action("read")
            if actor is not None
            else subject_model._base_manager.none()
        )
        record = instance_for_id(subject_model, id, queryset=queryset)
        if record is None:
            return None
        return field_provenance(record, actor) if isinstance(record, StatementsMixin) else []

    @strawberry.field
    def history(
        self, model: str, id: PublicID, limit: int = HISTORY_LIMIT_DEFAULT, offset: int = 0
    ) -> RecordHistoryType | None:
        """A record's changesets through its read gate; hidden and unknown records read as null."""

        _validate_page(limit, offset)
        tracked = history_model(model)
        actor = current_actor()
        queryset = (
            require_rebac_queryset(tracked).with_actor(actor).with_action("read")
            if actor is not None
            else tracked._base_manager.none()
        )
        record = instance_for_id(tracked, id, queryset=queryset)
        if record is None:
            return None
        return RecordHistory(record, actor).page(limit, offset)


@strawberry.type
class HistoryMutation:
    @strawberry.mutation
    @action_guard("Could not revert the changeset.")
    def revert_changeset(self, info: strawberry.Info, model: str, id: PublicID, changeset: PublicID) -> ActionResult:
        """Undo one complete boundary through the record's declared revert permission."""

        tracked = history_model(model)
        record = authorized_permission_target(
            info,
            cast(Any, tracked),
            id,
            tracked.history_revert_permission,
        )
        targets = Changeset._base_manager.db_manager(hints={"instance": record}).all()
        target = require_instance_for_id(Changeset, changeset, queryset=targets)
        if not target.revertable(record, actor=current_actor()):
            raise ValidationError("This changeset must be reverted from the owner of its complete write boundary.")
        reverted = target.revert()
        return ActionResult(ok=True, message="Changeset reverted.", id=PublicID(public_id_of(reverted)))


schemas = {
    "console": {
        "query": [HistoryQuery],
        "mutation": [HistoryMutation],
        "types": [
            RecordHistoryType,
            HistoryEntryType,
            HistoryFieldChangeType,
            HistoryReferenceType,
            HistoryStatementType,
            FieldProvenanceType,
            HistoryMergeType,
            HistoryRecordType,
        ],
    }
}
