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

from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, cast

import strawberry
from django.apps import apps
from django.conf import settings
from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import ValidationError
from django.db import models, router
from rebac import SubjectRef, current_actor
from rebac.field_visibility import gated_read_fields
from rebac.resources import model_for_subject_type
from strawberry.scalars import JSON

from angee.base.actors import source_kind_for
from angee.base.autoconfig import SETTINGS
from angee.base.identity import public_id_for, public_id_of
from angee.base.lineage import concrete_lineage
from angee.base.models import (
    HistoryMixin,
    SourceKind,
    StatementsMixin,
    model_history,
    record_history,
)
from angee.base.scoping import read_scoped_queryset
from angee.base.serialization import json_safe
from angee.graphql.actions import ActionResult, action_guard, authorized_permission_target
from angee.graphql.ids import PublicID, instance_for_id, require_instance_for_id
from angee.provenance.models import Changeset as ChangesetContract

Changeset = apps.get_model(getattr(settings, "ANGEE_CHANGESET_MODEL", SETTINGS["ANGEE_CHANGESET_MODEL"]))

HISTORY_LIMIT_DEFAULT = 25
HISTORY_LIMIT_MAX = 100

# The nested choices keep their owner's names on the wire; a bare ``Action`` would collide.
strawberry.enum(cast(Any, ChangesetContract.Action), name="ChangesetAction")
strawberry.enum(cast(Any, ChangesetContract.Activity), name="ChangesetActivity")
strawberry.enum(cast(Any, ChangesetContract.Outcome), name="ChangesetOutcome")
strawberry.enum(cast(Any, SourceKind), name="StatementSourceKind")


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
class HistoryStatementType:
    """One statement version the changeset wrote for the record, as stored."""

    id: PublicID
    aspect: str
    asserted_by: str
    source_kind: SourceKind
    snak: str
    rank: str
    status: str
    applied: bool
    confidence: float
    history_type: str
    values: JSON


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


def history_model(label: str) -> type[models.Model]:
    """Return the tracked, permission-governed model ``label`` names."""

    try:
        model = apps.get_model(label)
    except (LookupError, ValueError) as error:
        raise ValidationError(f"Unknown model {label!r}.") from error
    if record_history(model) is None:
        raise ValidationError(f"{model._meta.label} keeps no history.")
    return model


def history_tables(model: type[models.Model]) -> tuple[type[models.Model], ...]:
    """Return the tracked tables one row of ``model`` spans: itself and its MTI parents."""

    return tuple(table for table in concrete_lineage(model) if model_history(table) is not None)


@dataclass
class _Versions:
    """One table's versions of the record inside one changeset, with the row before them."""

    table: type[models.Model]
    first: Any
    last: Any
    previous: Any | None


@dataclass
class _Actors:
    """Per-request resolution of changeset actors to public references and source kinds."""

    using: str
    references: dict[str, tuple[str | None, str | None]] = field(default_factory=dict)
    kinds: dict[str, SourceKind | None] = field(default_factory=dict)

    def reference(self, actor: str) -> tuple[str | None, str | None]:
        if actor not in self.references:
            self.references[actor] = _actor_reference(SubjectRef.parse(actor), using=self.using)
        return self.references[actor]

    def kind(self, actor: str) -> SourceKind | None:
        if actor not in self.kinds:
            self.kinds[actor] = source_kind_for(SubjectRef.parse(actor))
        return self.kinds[actor]


def _actor_reference(actor: SubjectRef, *, using: str) -> tuple[str | None, str | None]:
    """Project a model-backed subject to its model label and public id."""

    resolved = model_for_subject_type(actor.subject_type)
    if resolved is None or not actor.subject_id or actor.subject_id == "*":
        return None, None
    model, id_attr = resolved
    pk = model._meta.pk
    if pk is not None and id_attr in ("pk", pk.name, pk.attname):
        try:
            return model._meta.label, public_id_for(model, pk.to_python(actor.subject_id))
        except TypeError, ValueError, ValidationError:
            return model._meta.label, None
    principal = model._base_manager.using(using).filter(**{id_attr: actor.subject_id}).first()
    return model._meta.label, public_id_of(principal) if principal is not None else None


def _reference_value(model_field: models.Field[Any, Any], value: Any) -> Any:
    """Return a JSON-safe value; a stored key becomes its target's public id."""

    if value is None:
        return None
    if model_field.is_relation and model_field.related_model is not None:
        return public_id_for(cast(type[models.Model], model_field.related_model), value)
    return json_safe(value)


class RecordHistory:
    """Read one record's changesets through its gate, redacted like the record itself."""

    def __init__(self, record: models.Model, actor: Any) -> None:
        self.record = record
        self.actor = actor
        self.model = type(record)
        self.using = record._state.db or router.db_for_read(self.model, instance=record)
        self.tables = history_tables(self.model)
        self.scopes = record.history_scope() if isinstance(record, HistoryMixin) else {
            table: models.Q(**{table._meta.pk.attname: record.pk}) for table in self.tables
        }
        # Pair evidence belongs to both endpoints. Only jointly readable pairs
        # may enter either record's timeline, including their generated statements.
        for owner in self.tables:
            if not issubclass(owner, StatementsMixin):
                continue
            for pair in owner.pair_models():
                endpoints = read_scoped_queryset(owner, actor).using(self.using).scoped().order_by().values("pk")
                predicate = (models.Q(left_id=record.pk) | models.Q(right_id=record.pk)) & models.Q(
                    left_id__in=models.Subquery(endpoints), right_id__in=models.Subquery(endpoints),
                )
                self.scopes[pair] = predicate
        self.actors = _Actors(using=self.using)

    # -- the record's changesets ---------------------------------------------------

    def changeset_ids(self) -> models.QuerySet[Any]:
        """The record's native history membership, without loading its lifetime into Python."""

        rows = [
            table.history.using(self.using).filter(predicate, changeset_id__isnull=False).order_by()
            .values_list("changeset_id", flat=True)
            for table, predicate in (*self.scopes.items(), *self.statement_scopes().items())
        ]
        # An unchanged survivor has no version of its own in a merge changeset.
        merged = Changeset._base_manager.using(self.using).filter(
            activity=ChangesetContract.Activity.MERGE,
            origin_content_type__in=[
                ContentType.objects.db_manager(self.using).get_for_model(table) for table in self.tables
            ],
            origin_object_id=str(self.record.pk),
        ).order_by().values_list("pk", flat=True)
        return merged.union(*rows)

    def changesets(self) -> models.QuerySet[Any]:
        """The record's changesets, newest first; ordering is the changeset owner's."""

        return Changeset._base_manager.using(self.using).filter(pk__in=models.Subquery(self.changeset_ids()))

    def entries(self, changesets: Iterable[Any]) -> list[HistoryEntryType]:
        """Project an already bounded window, also used by the combined Comments feed."""

        page = list(changesets)
        versions = self._versions([changeset.pk for changeset in page])
        writable = read_scoped_queryset(self.model, self.actor, action="write").using(self.using).filter(
            pk=self.record.pk,
        ).exists()
        return [self._entry(changeset, versions.get(changeset.pk, ()), writable) for changeset in page]

    def page(self, limit: int, offset: int) -> RecordHistoryType:
        changesets = self.changesets()
        return RecordHistoryType(
            model=self.model._meta.label,
            id=PublicID(public_id_of(self.record)),
            total=changesets.count(),
            entries=self.entries(changesets[offset : offset + limit]),
        )

    # -- versions ------------------------------------------------------------------

    def statement_scopes(self) -> dict[type[models.Model], models.Q]:
        """Statement tables share their subject's historical membership predicate."""

        scopes = {}
        for model, predicate in self.scopes.items():
            if not issubclass(model, StatementsMixin):
                continue
            subjects = model.history.using(self.using).filter(predicate).values(model._meta.pk.attname)
            scope = (models.Q(subject_id=self.record.pk) if model in self.tables
                     else models.Q(subject_id__in=models.Subquery(subjects)))
            for statement_model in model.statement_models():
                scopes[statement_model] = scopes[statement_model] | scope if statement_model in scopes else scope
        return scopes

    def revertable(self, changeset: Any) -> bool:
        """Whether this record admits the whole successful write boundary.

        An operational merge belongs to its survivor. Other operations must
        touch only this document's declared rows and statements. The changeset
        owner still checks every row's write permission and later writes.
        """

        if (changeset._state.db != self.using or changeset.state != ChangesetContract.State.CLOSED
                or changeset.outcome != ChangesetContract.Outcome.SUCCESS):
            return False
        origin = changeset.record_ref
        if (changeset.activity == ChangesetContract.Activity.MERGE
                and origin.model_label.lower() in {table._meta.label_lower for table in self.tables}
                and str(origin.object_id) == str(self.record.pk)):
            return True
        scopes = {**self.scopes, **self.statement_scopes()}
        touched = False
        for model, versions in changeset.history_rows():
            if not versions.exists():
                continue
            touched = True
            predicate = scopes.get(model)
            if predicate is None or versions.exclude(predicate).exists():
                return False
        return touched

    def _versions(self, changeset_ids: list[Any]) -> dict[Any, list[_Versions]]:
        """Fetch selected versions and one exact predecessor per write boundary."""

        grouped: dict[Any, list[_Versions]] = {}
        if not changeset_ids:
            return grouped
        for table, predicate in self.scopes.items():
            history = cast(Any, table).history.using(self.using)
            key = table._meta.pk.attname
            paged = history.filter(predicate, changeset_id__in=changeset_ids)
            predecessor = history.filter(
                **{key: models.OuterRef(key)}, history_id__lt=models.OuterRef("history_id"),
            ).order_by("-history_id").values("history_id")[:1]
            versions = history.filter(
                **{f"{key}__in": models.Subquery(paged.values(key))}, changeset_id__in=changeset_ids,
            ).annotate(_previous_id=models.Subquery(predecessor)).order_by(key, "history_id")
            selected: dict[tuple[Any, Any], _Versions] = {}
            for version in versions.iterator():
                identity = (getattr(version, key), version.changeset_id)
                state = selected.get(identity)
                if state is None:
                    selected[identity] = _Versions(table, version, version, None)
                else:
                    state.last = version
            previous = {row.history_id: row for row in history.filter(
                history_id__in=[state.first._previous_id for state in selected.values()
                                if state.first._previous_id is not None],
            )}
            for (_pk, changeset_id), state in selected.items():
                state.previous = previous.get(state.first._previous_id)
                grouped.setdefault(changeset_id, []).append(state)
        return grouped

    # -- entries -------------------------------------------------------------------

    def _attribution(self, changeset: Any) -> dict[str, Any]:
        if changeset._state.db != self.using:
            raise ValidationError("A history boundary must share its record's database.")
        actor_model, actor_id = self.actors.reference(changeset.actor) if changeset.actor else (None, None)
        kind = self.actors.kind(changeset.actor) if changeset.actor else None
        return dict(
            id=PublicID(public_id_of(changeset)),
            actor=changeset.actor or None,
            actor_model=actor_model,
            actor_id=PublicID(actor_id) if actor_id else None,
            source_kind=cast(Any, kind),
            action=cast(Any, ChangesetContract.Action(changeset.action)),
            activity=cast(Any, ChangesetContract.Activity(changeset.activity)),
            change_reason=changeset.change_reason,
            occurred_at=changeset.occurred_at,
            recorded_at=changeset.recorded_at,
            outcome=cast(Any, ChangesetContract.Outcome(changeset.outcome)) if changeset.outcome else None,
        )

    def summaries(self, changesets: Iterable[Any]) -> list[HistorySummaryType]:
        """Project only attribution for an already selected compact Comments window."""

        return [HistorySummaryType(**self._attribution(changeset)) for changeset in changesets]

    def _entry(self, changeset: Any, versions: Iterable[_Versions], writable: bool) -> HistoryEntryType:
        return HistoryEntryType(
            **self._attribution(changeset),
            changes=[change for state in versions for change in self._changes(state)],
            statements=list(self._statements(changeset)),
            merge=self._merge(changeset),
            can_revert=writable and self.revertable(changeset),
        )

    def _redacted(
        self, table: type[models.Model], pk: Any = None, *, snapshot: Any = None,
    ) -> frozenset[str]:
        """Use current row policy, or native snapshot gates for a deleted row."""

        pk = self.record.pk if pk is None else pk
        if table is self.model and pk == self.record.pk:
            row: Any = self.record
        else:
            using = self.using
            row = read_scoped_queryset(table, self.actor).using(using).filter(pk=pk).first()
            if row is None:
                hidden = frozenset(field.name for field in table._meta.concrete_fields)
                # A stored but unreadable row never gains access through older
                # relationship facts. Only genuinely deleted rows use snapshots.
                if snapshot is None or table._base_manager.using(using).filter(pk=pk).exists():
                    return hidden
                history = record_history(table)
                if history is None or not history.snapshot_allows(snapshot, "read", actor=self.actor):
                    return hidden
                return frozenset(
                    name for name in gated_read_fields(table)
                    if not history.snapshot_allows(snapshot, f"read__{name}", actor=self.actor)
                )
        return frozenset(getattr(row, "_rebac_redacted_fields", ()))

    def _changes(self, state: _Versions) -> Iterator[HistoryFieldChangeType]:
        """Diff the boundary's retained values; deleting a row changes them to null.

        A creation followed by deletion has no surviving before/after row.
        Deleted rows use their last snapshot for row and field permission gates;
        an existing row always keeps its current policy.
        """

        deleted = state.last.history_type == "-"
        if deleted and (state.first.history_type == "+" and state.previous is None
                        or state.previous is not None and state.previous.history_type == "-"):
            return
        table = state.table
        pk = getattr(state.last, table._meta.pk.attname)
        redacted = self._redacted(table, pk, snapshot=state.last)
        # Historical rows keep the source field names; ``diff_against`` reads them
        # through ``model_to_dict``, which knows editable fields only.
        fields = {
            model_field.name: model_field
            for model_field in table._meta.concrete_fields
            if model_field.editable
            and not model_field.primary_key
            and not (model_field.remote_field and model_field.remote_field.parent_link)
            and model_field.name not in redacted
        }
        included = [tracked.name for tracked in state.last.tracked_fields if tracked.name in fields]
        previous = state.previous if state.previous is not None else type(state.last)()
        current = state.last
        if deleted:
            previous = state.previous if state.previous is not None else state.last
            current = type(state.last)(**{fields[name].attname: None for name in included})
        for change in current.diff_against(previous, included_fields=included).changes:
            model_field = fields[change.field]
            yield HistoryFieldChangeType(
                field=model_field.name,
                model=table._meta.label,
                id=PublicID(public_id_for(table, pk)),
                old=_reference_value(model_field, change.old),
                new=_reference_value(model_field, change.new),
            )

    def _statements(self, changeset: Any) -> Iterator[HistoryStatementType]:
        """The record's statement versions this changeset wrote, values redacted like the subject."""

        for statement_model, predicate in self.statement_scopes().items():
            subject_model = statement_model.statement_subject_model
            versions = statement_model.history.using(self.using).filter(predicate, changeset_id=changeset.pk)
            for version in versions.order_by("history_id"):
                redacted = self._redacted(subject_model, version.subject_id)
                declaration = statement_model.statement_aspects[version.aspect]
                # A relation reads its stored key, never the related row.
                values = {
                    name: _reference_value(field, field.value_from_object(version))
                    for name in (*declaration.fields, *declaration.members)
                    if name not in redacted
                    for field in (statement_model._meta.get_field(name),)
                }
                yield HistoryStatementType(
                    id=PublicID(public_id_for(statement_model, version.id)),
                    aspect=version.aspect,
                    asserted_by=version.asserted_by,
                    source_kind=cast(Any, SourceKind(version.source_kind)),
                    snak=version.snak,
                    rank=version.rank,
                    status=version.status,
                    applied=version.applied,
                    confidence=version.confidence,
                    history_type=version.history_type,
                    values=cast(JSON, values),
                )

    def _merge(self, changeset: Any) -> HistoryMergeType | None:
        """The merge's origin and retired identities, excluding moved child rows."""

        if changeset.activity != ChangesetContract.Activity.MERGE:
            return None
        survivor = changeset.record_ref
        if not survivor.model_label:
            return None
        model = apps.get_model(survivor.model_label)
        survivor_pk = model._meta.pk.to_python(survivor.object_id)
        absorbed: dict[Any, HistoryRecordType] = {}
        for table in history_tables(model):
            key = table._meta.pk.attname
            retired = models.Q(history_type="-")
            if "merged_into" in {field.name for field in table._meta.fields}:
                retired |= models.Q(merged_into_id=survivor_pk)
            touched = cast(Any, table).history.using(self.using).filter(
                retired, changeset_id=changeset.pk,
            ).exclude(**{key: survivor_pk})
            for pk in touched.values_list(key, flat=True).distinct():
                absorbed.setdefault(pk, self._record(model, pk))
        return HistoryMergeType(
            survivor=self._record(model, survivor_pk),
            absorbed=[absorbed[pk] for pk in sorted(absorbed, key=str)],
        )

    def _record(self, model: type[models.Model], pk: Any) -> HistoryRecordType:
        return HistoryRecordType(
            model=model._meta.label,
            id=PublicID(public_id_for(model, pk)),
            retained=model._base_manager.using(self.using).filter(pk=pk).exists(),
        )


def _validate_page(limit: int, offset: int) -> None:
    if not 1 <= limit <= HISTORY_LIMIT_MAX or offset < 0:
        raise ValidationError(f"History requires a limit from 1 to {HISTORY_LIMIT_MAX} and a nonnegative offset.")


@strawberry.type
class HistoryQuery:
    @strawberry.field
    def history(
        self, model: str, id: PublicID, limit: int = HISTORY_LIMIT_DEFAULT, offset: int = 0
    ) -> RecordHistoryType | None:
        """A record's changesets through its read gate; hidden and unknown records read as null."""

        _validate_page(limit, offset)
        tracked = history_model(model)
        actor = current_actor()
        record = instance_for_id(tracked, id, queryset=read_scoped_queryset(tracked, actor))
        if record is None:
            return None
        return RecordHistory(record, actor).page(limit, offset)


@strawberry.type
class HistoryMutation:
    @strawberry.mutation
    @action_guard("Could not revert the changeset.")
    def revert_changeset(self, info: strawberry.Info, model: str, id: PublicID, changeset: PublicID) -> ActionResult:
        """Undo one of the record's changesets; ``write`` on the record admits, the owner restores."""

        tracked = history_model(model)
        record = authorized_permission_target(info, cast(Any, tracked), id, "write")
        history = RecordHistory(record, current_actor())
        target = require_instance_for_id(Changeset, changeset, queryset=Changeset._base_manager.using(history.using))
        if not history.changesets().filter(pk=target.pk).exists() or not history.revertable(target):
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
            HistoryStatementType,
            HistoryMergeType,
            HistoryRecordType,
        ],
    }
}
