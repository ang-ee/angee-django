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
from django.db import models
from rebac import SubjectRef, current_actor
from rebac.resources import model_for_subject_type
from strawberry.scalars import JSON

from angee.base.autoconfig import SETTINGS
from angee.base.identity import public_id_for, public_id_of
from angee.base.mixins import SourceKind, StatementsMixin, StatementSourceMixin
from angee.base.querysets import model_history, record_history
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


@strawberry.type
class HistoryEntryType:
    """One changeset in a record's timeline."""

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

    return tuple(table for table in (model, *model._meta.get_parent_list()) if model_history(table) is not None)


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

    references: dict[str, tuple[str | None, str | None]] = field(default_factory=dict)
    kinds: dict[str, SourceKind | None] = field(default_factory=dict)

    def reference(self, actor: str) -> tuple[str | None, str | None]:
        if actor not in self.references:
            self.references[actor] = _actor_reference(SubjectRef.parse(actor))
        return self.references[actor]

    def kind(self, actor: str) -> SourceKind | None:
        if actor not in self.kinds:
            self.kinds[actor] = StatementSourceMixin.kind_for(SubjectRef.parse(actor))
        return self.kinds[actor]


def _actor_reference(actor: SubjectRef) -> tuple[str | None, str | None]:
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
    principal = model._base_manager.filter(**{id_attr: actor.subject_id}).first()
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
        self.tables = history_tables(self.model)
        self.actors = _Actors()

    # -- the record's changesets ---------------------------------------------------

    def changeset_ids(self) -> set[Any]:
        """Every changeset that wrote a version of the record or merged into it."""

        ids: set[Any] = set()
        for table in self.tables:
            rows = cast(Any, table).history.filter(**self._key(table), changeset_id__isnull=False)
            ids.update(rows.values_list("changeset_id", flat=True).distinct())
        # A merge names its survivor as origin by the survivor's concrete class; an
        # unchanged survivor has no version of its own in that changeset.
        ids.update(
            Changeset._base_manager.filter(
                activity=ChangesetContract.Activity.MERGE,
                origin_content_type__in=[ContentType.objects.get_for_model(table) for table in self.tables],
                origin_object_id=str(self.record.pk),
            ).values_list("pk", flat=True)
        )
        return ids

    def changesets(self) -> models.QuerySet[Any]:
        """The record's changesets, newest first; ordering is the changeset owner's."""

        return Changeset._base_manager.filter(pk__in=self.changeset_ids())

    def page(self, limit: int, offset: int) -> RecordHistoryType:
        changesets = self.changesets()
        total = changesets.count()
        page = list(changesets[offset : offset + limit])
        versions = self._versions([changeset.pk for changeset in page])
        writable = read_scoped_queryset(self.model, self.actor, action="write").filter(pk=self.record.pk).exists()
        return RecordHistoryType(
            model=self.model._meta.label,
            id=PublicID(public_id_of(self.record)),
            total=total,
            entries=[self._entry(changeset, versions.get(changeset.pk, ()), writable) for changeset in page],
        )

    # -- versions ------------------------------------------------------------------

    def _key(self, table: type[models.Model]) -> dict[str, Any]:
        return {table._meta.pk.attname: self.record.pk}

    def _versions(self, changeset_ids: list[Any]) -> dict[Any, list[_Versions]]:
        """Group the page's versions by changeset, each with its pre-boundary row.

        The window spans every version from the row before the oldest paged
        version to the newest paged one, so a version written between two paged
        changesets still serves as the earlier one's predecessor.
        """

        grouped: dict[Any, list[_Versions]] = {}
        if not changeset_ids:
            return grouped
        for table in self.tables:
            history = cast(Any, table).history.filter(**self._key(table))
            paged = history.filter(changeset_id__in=changeset_ids).order_by("history_id")
            bounds = paged.aggregate(first=models.Min("history_id"), last=models.Max("history_id"))
            if bounds["first"] is None:
                continue
            before = history.filter(history_id__lt=bounds["first"]).order_by("-history_id").first()
            window = history.filter(history_id__gte=before.history_id if before else bounds["first"]).filter(
                history_id__lte=bounds["last"]
            )
            previous = None
            selected: dict[Any, _Versions] = {}
            for version in window.order_by("history_id"):
                if version.changeset_id in changeset_ids:
                    state = selected.get(version.changeset_id)
                    if state is None:
                        selected[version.changeset_id] = _Versions(table, version, version, previous)
                    else:
                        state.last = version
                previous = version
            for changeset_id, state in selected.items():
                grouped.setdefault(changeset_id, []).append(state)
        return grouped

    # -- entries -------------------------------------------------------------------

    def _entry(self, changeset: Any, versions: Iterable[_Versions], writable: bool) -> HistoryEntryType:
        actor_model, actor_id = self.actors.reference(changeset.actor) if changeset.actor else (None, None)
        kind = self.actors.kind(changeset.actor) if changeset.actor else None
        return HistoryEntryType(
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
            changes=[change for state in versions for change in self._changes(state)],
            statements=list(self._statements(changeset)),
            merge=self._merge(changeset),
            can_revert=writable
            and changeset.state == ChangesetContract.State.CLOSED
            and changeset.outcome == ChangesetContract.Outcome.SUCCESS,
        )

    def _redacted(self, table: type[models.Model]) -> frozenset[str]:
        """Field names the actor cannot read on this table's row, by the row's own projection."""

        if table is self.model:
            row: Any = self.record
        else:
            row = read_scoped_queryset(table, self.actor).filter(pk=self.record.pk).first()
            if row is None:
                return frozenset(field.name for field in table._meta.concrete_fields)
        return frozenset(getattr(row, "_rebac_redacted_fields", ()))

    def _changes(self, state: _Versions) -> Iterator[HistoryFieldChangeType]:
        """Diff the changeset's last version against the row before its first.

        A first version diffs against an empty snapshot; a deletion reports no
        deltas, the entry's action already says what happened.
        """

        if state.last.history_type == "-":
            return
        table = state.table
        redacted = self._redacted(table)
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
        for change in state.last.diff_against(previous, included_fields=included).changes:
            model_field = fields[change.field]
            yield HistoryFieldChangeType(
                field=model_field.name,
                old=_reference_value(model_field, change.old),
                new=_reference_value(model_field, change.new),
            )

    def _statements(self, changeset: Any) -> Iterator[HistoryStatementType]:
        """The record's statement versions this changeset wrote, values redacted like the subject."""

        if not isinstance(self.record, StatementsMixin):
            return
        for manager in self.record.statement_managers():
            statement_model = manager.model
            subject_model = statement_model.statement_subject_model
            redacted = self._redacted(subject_model)
            versions = statement_model.history.filter(subject_id=self.record.pk, changeset_id=changeset.pk)
            for version in versions.order_by("history_id"):
                declaration = statement_model.statement_aspects[version.aspect]
                values = {
                    name: _reference_value(statement_model._meta.get_field(name), getattr(version, name))
                    for name in (*declaration.fields, *declaration.members)
                    if name not in redacted
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
        """Survivor and absorbed records of a merge: the origin and the other rows it touched."""

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
            touched = cast(Any, table).history.filter(changeset_id=changeset.pk).exclude(**{key: survivor_pk})
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
            retained=model._base_manager.filter(pk=pk).exists(),
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
        target = require_instance_for_id(Changeset, changeset, queryset=Changeset._base_manager.all())
        if target.pk not in RecordHistory(record, current_actor()).changeset_ids():
            raise ValidationError("The changeset did not touch this record.")
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
