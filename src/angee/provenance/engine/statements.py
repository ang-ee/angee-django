"""Typed retained assertions generated through simple-history's native field copier.

Hosts own permission decisions and operational merges. Managers serialize assertion
selection on the canonical subject and keep superseded and withdrawn evidence.
"""

from __future__ import annotations

import importlib
from collections.abc import Callable, Iterable, Iterator, Mapping, Sequence
from contextlib import AbstractContextManager, contextmanager, nullcontext
from contextvars import ContextVar
from copy import copy
from dataclasses import dataclass
from typing import Any, ClassVar, cast

from django.core.exceptions import ImproperlyConfigured, ValidationError
from django.db import models, router, transaction
from django.db.models import Q
from simple_history.manager import HistoryDescriptor
from simple_history.models import HistoricalRecords

from angee.provenance.context import actor_context, actor_reference, current_actor
from angee.provenance.engine.policy import get_policy
from angee.provenance.engine.replay import HistoryReplayMixin
from angee.provenance.evidence import evidence_values
from angee.provenance.history import ModelHistory
from angee.provenance.history import ReplayHistoryMixin as HistoryMixin
from angee.provenance.querysets import HistoryQuerySet


class SourceKind(models.TextChoices):
    PERSON = "person", "Person"
    SYSTEM_OF_RECORD = "system_of_record", "System of record"
    DIRECTORY = "directory", "Directory"
    NETWORK = "network", "Network"
    IMPORT = "import", "Import"
    SEED = "seed", "Seed"
    RULE = "rule", "Rule"
    EXTRACTION = "extraction", "Extraction"
    DOCUMENT = "document", "Document"


class StatementSourceMixin(models.Model):
    """A host principal declares its source classification through source_kind."""

    class Meta:
        abstract = True

    @property
    def source_kind(self) -> str | None:
        raise NotImplementedError("The principal must declare its source_kind.")


class StatementLinkMixin(models.Model):
    """A host link may identify a system-of-record assertion as authoritative."""

    class Meta:
        abstract = True

    @property
    def authoritative(self) -> bool:
        return False


@dataclass(frozen=True)
class Aspect[SubjectT: models.Model]:
    """Typed canonical fields and optional member identity selected as one fact.

    member accepts an existing field name or a mapping of new native Django
    fields. apply(subject, statement) owns effects for multivalued/pair aspects.
    candidates(subject, rows) can restrict eligible retained assertions.
    """

    fields: tuple[str, ...] = ()
    member: str | Mapping[str, models.Field[Any, Any]] | None = None
    precedence: tuple[str, ...] = ()
    allow_fill: bool = False
    candidates: Callable[[SubjectT, Sequence[Any]], Sequence[Any]] | None = None
    apply: Callable[[SubjectT, Any], None] | None = None

    @property
    def members(self) -> tuple[str, ...]:
        return (self.member,) if isinstance(self.member, str) else tuple(sorted(self.member or ()))

    def priority(self, row: Any) -> int:
        """Authoritative system-of-record follows preferred rank but can precede persons."""
        order = self.precedence
        index = order.index(row.source_kind) if row.source_kind in order else len(order)
        if SourceKind.PERSON in order and SourceKind.SYSTEM_OF_RECORD in order:
            person = order.index(SourceKind.PERSON)
            if order.index(SourceKind.SYSTEM_OF_RECORD) > person:
                if row.source_kind == SourceKind.SYSTEM_OF_RECORD and row.authoritative:
                    return person
                return index + (index >= person)
        return index

    def resolve(self, subject: SubjectT, rows: Sequence[Any]) -> tuple[Any, dict[str, Any] | None]:
        """Select one retained stance and project its typed values without writing."""
        if not rows:
            return None, None
        asserted = [row for row in rows if row.status == "asserted"]
        normal = [row for row in asserted if row.rank == "normal"]
        if self.candidates is not None:
            admitted = {row.pk for row in normal}
            normal = list(self.candidates(subject, normal))
            if any(row.pk not in admitted or row.rank != "normal" or row.status != "asserted" for row in normal):
                raise ValidationError("Candidate policy may only select asserted normal statements.")
        preferred = sorted((row for row in asserted if row.rank == "preferred"), key=lambda row: row.sort_key(self))
        normal.sort(key=lambda row: row.sort_key(self))
        winner = preferred[0] if preferred else next((row for row in normal if row.snak == "value"), None)
        if (winner is not None and winner.snak == "some_value") or (
            winner is None and any(row.snak == "some_value" for row in asserted)
        ):
            return winner, None
        values = {
            name: winner._meta.get_field(name).value_from_object(winner)
            if winner is not None and winner.snak == "value"
            else None
            for name in self.fields
        }
        if winner is not None and winner.snak == "value" and self.allow_fill:
            for name, value in values.items():
                if value in (None, ""):
                    values[name] = next(
                        (
                            row._meta.get_field(name).value_from_object(row)
                            for row in normal
                            if row.snak == "value"
                            and row._meta.get_field(name).value_from_object(row) not in (None, "")
                        ),
                        value,
                    )
        return winner, values


_mutating: ContextVar[bool] = ContextVar("provenance_statement_mutating", default=False)
_applying: ContextVar[bool] = ContextVar("provenance_statement_applying", default=False)


@contextmanager
def statement_application() -> Iterator[None]:
    """Suppress automatic person assertions during host projection or replay.

    This does not authorize writes or unlock retained assertion mutation.
    """
    token = _applying.set(True)
    try:
        yield
    finally:
        _applying.reset(token)


@contextmanager
def _statement_write() -> Iterator[None]:
    token = _mutating.set(True)
    try:
        yield
    finally:
        _mutating.reset(token)


class StatementQuerySet(HistoryQuerySet[Any]):
    """Retained scopes; arbitrary mutation cannot bypass assertion ownership."""

    def current(self) -> Any:
        return self.filter(superseded__isnull=True)

    def applied(self) -> Any:
        return self.current().filter(applied=True)

    def provenance(self, field: str) -> Any:
        aspects = [name for name, aspect in self.model.statement_aspects.items() if field in aspect.fields]
        if not aspects:
            raise ValidationError(f"No assertion aspect owns field {field!r}.")
        return self.applied().filter(aspect__in=aspects)

    def update(self, **kwargs: Any) -> int:
        if not _mutating.get():
            raise ValidationError("Use statement manager verbs to change retained assertions.")
        return super().update(**kwargs)

    def bulk_create(self, *args: Any, **kwargs: Any) -> Any:
        if not _mutating.get():
            raise ValidationError("Use statements.set() to assert values.")
        return super().bulk_create(*args, **kwargs)

    def bulk_update(self, *args: Any, **kwargs: Any) -> int:
        if not _mutating.get():
            raise ValidationError("Use statement manager verbs to change retained assertions.")
        return super().bulk_update(*args, **kwargs)

    def delete(self) -> Any:
        raise ValidationError("Statements retain evidence; withdraw instead of deleting.")


class StatementWriteMixin:
    """Shared admitted-write engine for native managers with explicit storage policies."""

    model: Any

    @staticmethod
    def _column_value(field: Any, value: Any, alias: str) -> Any:
        if not isinstance(field, models.ForeignKey):
            return field.clean(value, None)
        if isinstance(value, models.Model):
            if not isinstance(value, field.remote_field.model) or value._state.adding or value._state.db != alias:
                raise ValidationError("Assertion references must be persisted on the subject database.")
            value = field.target_field.value_from_object(value)
        value = field.target_field.to_python(value)
        if (
            value is not None
            and not field.remote_field.model._base_manager.using(alias)
            .filter(**{field.target_field.name: value})
            .exists()
        ):
            raise ValidationError(f"Unknown assertion reference {field.name}.")
        return value

    def _assertion_write_context(self) -> AbstractContextManager[Any]:
        """Native mutation boundary after subject/source admission has completed."""
        return _statement_write()

    def _write_assertion(
        self,
        subject: Any,
        reference: str,
        definition: Aspect[models.Model],
        identity: Mapping[str, Any],
        content: Mapping[str, Any],
        *,
        using: str,
    ) -> Any:
        """Validate and retain one admitted stance through the host's representation policy."""
        columns = {}
        for name, value in content.items():
            field = self.model._meta.get_field(name)
            if field.name in definition.fields:
                name = field.attname
                value = self._column_value(field, value, using)
            columns[name] = value
        content = columns
        row = self.model()
        row._state.db = using
        for name, value in {
            "subject": subject,
            "asserted_by": reference,
            **identity,
            **content,
        }.items():
            setattr(row, name, value)
        row.full_clean(validate_unique=False, validate_constraints=False)
        previous = (
            self.model._base_manager.using(using)
            .filter(subject=subject, asserted_by=reference, superseded__isnull=True, **identity)
            .first()
        )
        if previous is not None and all(
            row._meta.get_field(name).value_from_object(previous) == row._meta.get_field(name).value_from_object(row)
            for name in content
        ):
            return previous
        with self._assertion_write_context():
            row = self._store_assertion(row, previous, content=content, using=using)
            return self._resolve_assertion(row, subject, definition, identity, using=using)

    def _store_assertion(self, row: Any, previous: Any, *, content: Mapping[str, Any], using: str) -> Any:
        """Default native representation retains a new row and supersedes its predecessor."""
        if previous is not None:
            previous.applied = False
            previous.superseded = previous
            previous.save(using=using, update_fields=["applied", "superseded"])
        row.save(using=using)
        if previous is not None:
            previous.superseded = row
            previous.save(using=using, update_fields=["superseded"])
        return row

    def _resolve_assertion(
        self,
        row: Any,
        subject: Any,
        definition: Aspect[models.Model],
        identity: Mapping[str, Any],
        *,
        using: str,
    ) -> Any:
        raise NotImplementedError("An assertion manager must resolve its admitted write.")


class StatementManager(StatementWriteMixin, models.Manager.from_queryset(StatementQuerySet)):  # type: ignore[misc]
    """Subject-bound mutation and deterministic selection under a subject lock."""

    def __init__(self, model: Any = None, instance: Any = None) -> None:
        super().__init__()
        if model is not None:
            self.model = model
        self.instance = instance

    def get_queryset(self) -> Any:
        rows = super().get_queryset()
        return rows.filter(subject=self.instance) if self.instance is not None else rows

    def _write_alias(self) -> str:
        return self._db or router.db_for_write(self.model, instance=self.instance)

    def _subject(self, actor: Any) -> tuple[Any, str]:
        if self.instance is None or self.instance.pk is None or self.instance._state.adding:
            raise ValidationError("Statement mutations require a persisted subject.")
        alias = self._write_alias()
        if self.instance._state.db != alias:
            raise ValidationError("Statement subject and assertion must use the same database.")
        subject = type(self.instance)._base_manager.using(alias).select_for_update().get(pk=self.instance.pk)
        get_policy().authorize(actor, "change", subject)
        return subject, alias

    def _principal(self, source: Any, alias: str) -> tuple[Any, str, str]:
        """Validate persisted source identity even for an empty collection mutation."""
        stored = get_policy().resolve_source(source, using=alias)
        kind = get_policy().source_kind(stored)
        if kind not in SourceKind.values:
            raise ValidationError("The asserting source has no recognized source kind.")
        return stored, actor_reference(stored), str(kind)

    def _source(self, source: Any, alias: str, subject: Any) -> tuple[str, str]:
        stored, reference, kind = self._principal(source, alias)
        get_policy().authorize(stored, "change", subject)
        return reference, kind

    def _identity(
        self, aspect: str, members: Mapping[str, Any], alias: str
    ) -> tuple[Aspect[models.Model], dict[str, Any]]:
        try:
            definition = self.model.statement_aspects[aspect]
        except KeyError as error:
            raise ValidationError(f"Unknown statement aspect {aspect!r}.") from error
        if set(members) != set(definition.members):
            raise ValidationError("Every aspect member, and no other member, is required.")
        identity: dict[str, Any] = {"aspect": aspect}
        for name, value in members.items():
            if value is None:
                raise ValidationError("Aspect members cannot be null.")
            field = self.model._meta.get_field(name)
            identity[field.attname] = self._column_value(field, value, alias)
        return definition, identity

    def set(
        self,
        asserted_by: Any = None,
        *,
        aspect: str,
        values: Mapping[str, Any] | None = None,
        rank: str = "normal",
        snak: str = "value",
        status: str = "asserted",
        confidence: float | None = None,
        link: Any = None,
        evidence: Any = (),
        basis_hash: str = "",
        **members: Any,
    ) -> Any:
        """Retain a new assertion and supersede the source's former current stance."""
        actor = current_actor()
        source = actor if asserted_by is None else asserted_by
        alias = self._write_alias()
        with transaction.atomic(using=alias):
            subject, alias = self._subject(actor)
            reference, kind = self._source(source, alias, subject)
            definition, identity = self._identity(aspect, members, alias)
            if (
                rank not in Statement.Rank.values
                or snak not in Statement.Snak.values
                or status not in Statement.Status.values
            ):
                raise ValidationError("Invalid rank, stance or status.")
            if rank == Statement.Rank.PREFERRED and kind not in (
                SourceKind.PERSON,
                SourceKind.SEED,
            ):
                raise ValidationError("Only person and seed sources may write preferred assertions.")
            supplied = dict(values or {})
            if set(supplied) - set(definition.fields):
                raise ValidationError("Assertion values must belong to the declared aspect.")
            if snak != Statement.Snak.VALUE and supplied:
                raise ValidationError("Unknown/absent stances cannot carry asserted values.")
            if confidence is not None and not 0 <= confidence <= 1:
                raise ValidationError("Confidence must be between zero and one.")
            if link is not None:
                if not self.model.statement_link or not isinstance(link, StatementLinkMixin):
                    raise ValidationError("This declaration requires a configured StatementLinkMixin link.")
                if link._state.adding or link._state.db != alias:
                    raise ValidationError("The assertion link must be persisted on the subject database.")
            encoded = evidence_values(evidence)
            content = {
                **{name: supplied.get(name) for name in definition.fields},
                "source_kind": kind,
                "rank": rank,
                "snak": snak,
                "status": status,
                "confidence": confidence,
                "evidence": encoded,
                "basis_hash": basis_hash,
            }
            if self.model.statement_link:
                content["link_id"] = link.pk if link is not None else None
            return self._write_assertion(subject, reference, definition, identity, content, using=alias)

    def _resolve_assertion(
        self,
        row: Any,
        subject: Any,
        definition: Aspect[models.Model],
        identity: Mapping[str, Any],
        *,
        using: str,
    ) -> Any:
        winner = self._resolve(subject, using, definition, identity)
        row.applied = winner is not None and winner.pk == row.pk
        return row

    def _resolve(
        self,
        subject: Any,
        alias: str,
        definition: Aspect[models.Model],
        identity: Mapping[str, Any],
    ) -> Any:
        selection = self.get_queryset().using(alias).filter(**identity, superseded__isnull=True)
        if self.model.statement_link:
            selection = selection.select_related("link")
        retained = list(selection)
        winner, values = definition.resolve(subject, retained)
        for row in sorted(retained, key=lambda row: row is winner):
            if row.applied != (row is winner):
                row.applied = row is winner
                row.save(using=alias, update_fields=["applied"])
        if values is None:
            return winner
        if definition.apply:
            definition.apply(subject, winner)
        elif not definition.members:
            updates: dict[str, Any] = {}
            for name in definition.fields:
                field = subject._meta.get_field(name)
                value = values[name]
                if value is None and not field.null:
                    value = field.get_default() if field.has_default() else "" if field.empty_strings_allowed else None
                updates[field.attname] = value
            changed = [name for name, value in updates.items() if getattr(subject, name) != value]
            for name, value in updates.items():
                setattr(subject, name, value)
                if self.instance is not None:
                    setattr(self.instance, name, value)
            if changed:
                subject.full_clean()
                subject.save(using=alias, update_fields=changed)
        return winner

    def resolve(self, *, actor: Any = None, aspect: str | None = None, **members: Any) -> list[Any]:
        """Recompute declared identities, preserving fields with no assertions."""
        actor = current_actor() if actor is None else actor
        alias = self._write_alias()
        with transaction.atomic(using=alias), _statement_write():
            subject, alias = self._subject(actor)
            result = []
            for name, definition in self.model.statement_aspects.items():
                if aspect is not None and name != aspect:
                    continue
                if members or not definition.members:
                    _, identity = self._identity(name, members, alias)
                    identities = [identity]
                else:
                    columns = [self.model._meta.get_field(member).attname for member in definition.members]
                    identities = [
                        {"aspect": name, **identity}
                        for identity in self.get_queryset().using(alias).filter(aspect=name).values(*columns).distinct()
                    ]
                for identity in identities:
                    result.append(self._resolve(subject, alias, definition, identity))
            return result

    def withdraw(self, asserted_by: Any = None, subjects: Sequence[Any] | None = None, *, link: Any = None) -> int:
        """Withdraw this source's current assertions without deleting evidence."""
        actor = current_actor()
        alias = self._write_alias()
        if subjects is not None:
            with transaction.atomic(using=alias):
                self._principal(actor if asserted_by is None else asserted_by, alias)
                targets = list(subjects)
                if any(
                    not isinstance(subject, self.model.statement_subject_model) or subject._state.db != alias
                    for subject in targets
                ):
                    raise ValidationError("Withdrawal subjects must share the assertion database.")
                return sum(
                    type(self)(self.model, subject).db_manager(alias).withdraw(asserted_by, link=link)
                    for subject in sorted(targets, key=lambda subject: subject.pk)
                )
        with transaction.atomic(using=alias), _statement_write():
            subject, alias = self._subject(actor)
            reference, _ = self._source(actor if asserted_by is None else asserted_by, alias, subject)
            rows = (
                self.get_queryset()
                .using(alias)
                .filter(asserted_by=reference, superseded__isnull=True, status="asserted")
            )
            if link is not None:
                rows = rows.filter(link=link)
            count = rows.update(status="withdrawn", applied=False)
            self.resolve(actor=actor)
            return int(count)

    def withdraw_unsupported(self, asserted_by: Any, supported: Sequence[Any], *, link: Any = None) -> int:
        """Withdraw current assertions whose subject is absent from the supported set."""
        alias = self._write_alias()
        with transaction.atomic(using=alias):
            _, reference, _ = self._principal(asserted_by, alias)
            keys = [value.pk if isinstance(value, models.Model) else value for value in supported]
            rows = (
                self.get_queryset()
                .using(alias)
                .filter(
                    asserted_by=reference,
                    superseded__isnull=True,
                    status="asserted",
                )
                .exclude(subject_id__in=keys)
            )
            if link is not None:
                rows = rows.filter(link=link)
            subjects = self.model.statement_subject_model._base_manager.using(alias).filter(
                pk__in=rows.values("subject_id"),
            )
            return int(self.db_manager(alias).withdraw(asserted_by, subjects=subjects, link=link))

    def confirm(self, *, aspect: str, values: Mapping[str, Any], **members: Any) -> Any:
        return self.set(aspect=aspect, values=values, rank="preferred", **members)

    def dismiss(self, *, aspect: str, **members: Any) -> Any:
        return self.set(aspect=aspect, rank="preferred", snak="no_value", **members)


class StatementHistory(ModelHistory):
    """Replay retained assertions through their canonical subject's policy."""

    def authorize_revert(self, first: Any, last: Any, previous: Any, actor: Any) -> None:
        row = last.instance
        get_policy().authorize(actor, "change", row.subject)
        if row.statement_pair:
            get_policy().authorize(actor, "change", row.other)

    def revert_order(self, first: Any, last: Any, previous: Any) -> tuple[Any, ...]:
        return (1, first.instance._meta.label_lower, str(first.instance.pk))

    def revert_removal_collector(self, first: Any, last: Any, previous: Any) -> None:
        return None

    def stage_revert(self, first: Any, last: Any, previous: Any) -> None:
        row = last.instance
        with _statement_write():
            type(row)._base_manager.using(last._state.db).filter(pk=row.pk).update(applied=False, superseded_id=row.pk)

    def revert_row(self, first: Any, last: Any, previous: Any) -> list[Any]:
        row = previous.instance if previous is not None else last.instance
        with _statement_write():
            if previous is None:
                row.status = "withdrawn"
                row.applied = False
                row.superseded_id = row.pk
            row.save(using=last._state.db)
        return [row.subject, row.other] if row.statement_pair else [row.subject]


class Statement(HistoryMixin):
    """Retained assertion base. Mutation belongs exclusively to manager verbs."""

    class Rank(models.TextChoices):
        NORMAL = "normal", "Normal"
        PREFERRED = "preferred", "Preferred"

    class Snak(models.TextChoices):
        VALUE = "value", "Value"
        SOME_VALUE = "some_value", "Some value"
        NO_VALUE = "no_value", "No value"

    class Status(models.TextChoices):
        ASSERTED = "asserted", "Asserted"
        WITHDRAWN = "withdrawn", "Withdrawn"
        QUARANTINED = "quarantined", "Quarantined"
        DEPRECATED = "deprecated", "Deprecated"

    history = StatementHistory(inherit=True)
    objects: ClassVar[Any] = StatementManager()
    asserted_by = models.CharField(max_length=1024)
    source_kind = models.CharField(max_length=32, choices=SourceKind.choices)
    rank = models.CharField(max_length=16, choices=Rank.choices, default=Rank.NORMAL)
    snak = models.CharField(max_length=16, choices=Snak.choices, default=Snak.VALUE)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.ASSERTED)
    confidence = models.FloatField(null=True, blank=True)
    applied = models.BooleanField(default=False)
    superseded = models.ForeignKey("self", null=True, blank=True, on_delete=models.PROTECT, related_name="+")
    evidence = models.JSONField(default=list, blank=True)
    basis_hash = models.CharField(max_length=64, blank=True)
    statement_aspects: ClassVar[dict[str, Aspect[models.Model]]]
    statement_subject_model: ClassVar[type[models.Model]]
    statement_link: ClassVar[str | None]
    statement_pair: ClassVar[bool] = False

    class Meta(HistoryMixin.Meta):
        abstract = True
        base_manager_name = "objects"
        constraints = [
            models.CheckConstraint(
                condition=Q(rank="normal") | Q(source_kind__in=("person", "seed")),
                name="%(app_label)s_%(class)s_preferred",
            ),
            models.CheckConstraint(
                condition=Q(confidence__isnull=True) | Q(confidence__gte=0, confidence__lte=1),
                name="%(app_label)s_%(class)s_confidence",
            ),
        ]

    def sort_key(self, aspect: Aspect[models.Model] | None = None) -> tuple[Any, ...]:
        definition = self.statement_aspects[cast(Any, self).aspect] if aspect is None else aspect
        return definition.priority(self), -(self.confidence or 0), self.asserted_by, -self.pk

    @property
    def authoritative(self) -> bool:
        return bool(self.statement_link and cast(Any, self).link_id is not None and cast(Any, self).link.authoritative)

    @property
    def is_confirmed(self) -> bool:
        return self.rank == self.Rank.PREFERRED and self.snak == self.Snak.VALUE and self.status == self.Status.ASSERTED

    @property
    def is_dismissed(self) -> bool:
        return (
            self.rank == self.Rank.PREFERRED and self.snak == self.Snak.NO_VALUE and self.status == self.Status.ASSERTED
        )

    def save(self, *args: Any, **kwargs: Any) -> None:
        if not _mutating.get():
            raise ValidationError("Use statements.set() or a statement transition.")
        super().save(*args, **kwargs)

    def delete(self, *args: Any, **kwargs: Any) -> Any:
        raise ValidationError("Statements retain evidence; withdraw instead of deleting.")


class StatementEditMixin(models.Model):
    """Capture ordinary person edits through declared managers without owning capture or replay."""

    class Meta:
        abstract = True

    def _statement_editor(self, *, using: str) -> models.Model | None:
        actor = current_actor()
        return (
            actor if isinstance(actor, models.Model) and get_policy().source_kind(actor) == SourceKind.PERSON else None
        )

    def _statement_edit_context(self, editor: models.Model) -> AbstractContextManager[Any]:
        return nullcontext()

    def _edit_managers(self, *, using: str) -> Iterable[Any]:
        for model in self.statement_models():
            yield StatementManager(model, self).db_manager(using)

    @classmethod
    def statement_models(cls) -> Iterable[Any]:
        raise NotImplementedError("An assertion subject must declare its statement tables.")

    def save(self, *args: Any, **kwargs: Any) -> None:
        using = kwargs.get("using") or self._state.db or router.db_for_write(type(self), instance=self)
        editor = None if _mutating.get() or _applying.get() else self._statement_editor(using=using)
        if editor is None:
            super().save(*args, **kwargs)
            return
        selected = kwargs.get("update_fields")
        saved_fields = (
            None
            if selected is None
            else {
                field.name
                for field in self._meta.concrete_fields
                if field.name in selected or field.attname in selected
            }
        )
        unwritten = (
            {}
            if saved_fields is None
            else {
                field.attname: getattr(self, field.attname)
                for field in self._meta.concrete_fields
                if field.name not in saved_fields
            }
        )
        with (
            self._statement_edit_context(editor),
            actor_context(editor),
            transaction.atomic(using=using),
        ):
            previous = type(self)._base_manager.using(using).filter(pk=self.pk).first() if self.pk is not None else None
            kwargs["using"] = using
            super().save(*args, **kwargs)
            stored = type(self)._base_manager.using(using).get(pk=self.pk)
            resolved_fields: set[str] = set()
            for manager in self._edit_managers(using=using):
                for name, definition in manager.model.statement_aspects.items():
                    if (
                        definition.members
                        or not definition.fields
                        or (definition.precedence and SourceKind.PERSON not in definition.precedence)
                    ):
                        continue
                    values = {}
                    changed = False
                    for field_name in definition.fields:
                        field = stored._meta.get_field(field_name)
                        if not isinstance(field, models.Field):
                            raise ImproperlyConfigured("Assertion values require forward model fields.")
                        values[field_name] = field.value_from_object(stored)
                        changed |= (saved_fields is None or field_name in saved_fields) and (
                            previous is None or field.value_from_object(previous) != values[field_name]
                        )
                    if not changed or (previous is None and all(value in (None, "") for value in values.values())):
                        continue
                    manager.set(asserted_by=editor, aspect=name, values=values)
                    resolved_fields.update(
                        definition.fields if saved_fields is None else set(definition.fields) & saved_fields
                    )
            if resolved_fields:
                self.refresh_from_db(using=using, fields=sorted(resolved_fields))
            for attname, value in unwritten.items():
                setattr(self, attname, value)


class StatementsMixin(StatementEditMixin, HistoryReplayMixin):
    """Canonical subject application and discovery of native assertion declarations."""

    class Meta(StatementEditMixin.Meta, HistoryReplayMixin.Meta):
        abstract = True
        base_manager_name = "objects"

    @classmethod
    def statement_models(cls) -> Iterable[Any]:
        seen = set()
        for owner in cls.__mro__:
            for value in owner.__dict__.values():
                if (
                    isinstance(value, HistoryDescriptor)
                    and issubclass(value.model, Statement)
                    and value.model not in seen
                ):
                    seen.add(value.model)
                    yield value.model

    def history_identity_columns(self) -> dict[Any, tuple[str, ...]]:
        result = super().history_identity_columns()
        result.update(
            {
                model: ("subject_id", "other_id") if model.statement_pair else ("subject_id",)
                for model in self.statement_models()
            }
        )
        return result

    def resolve_statements(self) -> None:
        for model in self.statement_models():
            StatementManager(model, self).resolve()

    def provenance(self, field: str) -> Any:
        for model in self.statement_models():
            aspects = [name for name, aspect in model.statement_aspects.items() if field in aspect.fields]
            if aspects:
                return (
                    StatementManager(model, self)
                    .get_queryset()
                    .filter(aspect__in=aspects, applied=True, superseded__isnull=True)
                )
        raise ValidationError(f"No assertion aspect owns field {field!r}.")

    def confirm(self, *, aspect: str, values: Mapping[str, Any], **members: Any) -> Any:
        return self.statements_for(aspect).confirm(aspect=aspect, values=values, **members)

    def dismiss(self, *, aspect: str, **members: Any) -> Any:
        return self.statements_for(aspect).dismiss(aspect=aspect, **members)

    def statements_for(self, aspect: str) -> Any:
        owners = [model for model in self.statement_models() if aspect in model.statement_aspects]
        if len(owners) != 1:
            raise ValidationError("An assertion aspect must have exactly one declaration owner.")
        return StatementManager(owners[0], self)


class StatementRecords[SubjectT: models.Model = models.Model](HistoricalRecords):
    """Generate retained typed assertion models through native simple-history hooks."""

    def __init__(
        self,
        *,
        aspects: Mapping[str, Aspect[SubjectT]],
        link: str | None = None,
        model_name: str | None = None,
        bases: Sequence[type[models.Model]] = (Statement,),
        history_manager: type[models.Manager[Any]] = StatementManager,
        historical_queryset: type[models.QuerySet[Any]] = StatementQuerySet,
        excluded_fields: Sequence[str] = (),
    ) -> None:
        self.aspects = dict(aspects)
        self.link = link
        self.model_name = model_name
        super().__init__(
            inherit=True,
            bases=bases,
            history_manager=cast(Any, history_manager),
            historical_queryset=cast(Any, historical_queryset),
            excluded_fields=excluded_fields,
            custom_model_name=(lambda name: model_name or f"{name}Statement"),
        )

    def contribute_to_class(self, cls: Any, name: str) -> None:
        super().contribute_to_class(cls, name)
        setattr(cls, name, self)

    def aspects_for(self, model: Any) -> dict[str, Aspect[SubjectT]]:
        result: dict[str, Aspect[SubjectT]] = {}
        for owner in reversed(model.__mro__):
            declaration = owner.__dict__.get(self.manager_name)
            if isinstance(declaration, StatementRecords):
                for key, aspect in declaration.aspects.items():
                    if key in result and result[key] != aspect:
                        raise ImproperlyConfigured(f"Conflicting assertion aspect {key!r}.")
                    result[key] = aspect
        return result

    def declaration_for(self, model: Any) -> StatementRecords[SubjectT] | None:
        return next(
            (
                owner.__dict__[self.manager_name]
                for owner in model.__mro__
                if isinstance(owner.__dict__.get(self.manager_name), StatementRecords)
            ),
            None,
        )

    def finalize(self, sender: Any, **kwargs: Any) -> None:
        if sender._meta.abstract or sender._meta.proxy or not issubclass(sender, self.cls):
            return
        declaration = self.declaration_for(sender)
        if declaration is not self:
            return
        module = importlib.import_module(sender.__module__)
        name = str(self.get_history_model_name(sender))
        if name.lower() in sender._meta.apps.all_models[sender._meta.app_label] or hasattr(module, name):
            raise ImproperlyConfigured(f"Generated assertion model {name} already exists.")
        model = self.create_history_model(sender, inherited=sender is not self.cls)
        setattr(module, name, model)
        setattr(
            sender,
            self.manager_name,
            HistoryDescriptor(model, self.history_manager, self.historical_queryset),
        )

    def fields_included(self, model: Any) -> list[Any]:
        fields: dict[str, Any] = {}
        reserved = {
            field.name for base in self.bases if issubclass(base, models.Model) for field in base._meta.fields
        } | {"subject", "aspect", "link"}
        for definition in self.aspects_for(model).values():
            for name in (*definition.fields, *definition.members):
                if name in self.excluded_fields:
                    continue
                if name in reserved:
                    raise ImproperlyConfigured(f"Assertion field {name!r} conflicts with a retained assertion column.")
                field = (
                    copy(definition.member[name])
                    if isinstance(definition.member, Mapping) and name in definition.member
                    else model._meta.get_field(name)
                )
                if field.primary_key or field.many_to_many or isinstance(field, models.GeneratedField):
                    raise ImproperlyConfigured(f"{name} is not a writable assertion column.")
                if not hasattr(field, "model"):
                    field.set_attributes_from_name(name)
                    field.model = model
                fields[name] = field
        return [fields[name] for name in sorted(fields)]

    def copy_fields(self, model: Any) -> dict[str, Any]:
        fields = super().copy_fields(model)
        for field in fields.values():
            field.null = field.blank = True
            field.default = field.db_default = models.NOT_PROVIDED
            field.auto_created = False
            if isinstance(field, models.ForeignKey):
                field.remote_field.on_delete = models.PROTECT
                field.remote_field.parent_link = False
                cast(Any, field).db_constraint = True
        return cast(dict[str, Any], fields)

    def get_extra_fields(self, model: Any, fields: Any) -> dict[str, Any]:
        return {
            "__module__": model.__module__,
            "statement_subject_model": model,
            "statement_aspects": self.aspects_for(model),
            "statement_link": self.link,
            "subject": models.ForeignKey(model, on_delete=models.PROTECT, related_name="+"),
            "aspect": models.CharField(
                max_length=64, choices=[(name, name) for name in sorted(self.aspects_for(model))]
            ),
            **(
                {
                    "link": models.ForeignKey(
                        self.link, null=True, blank=True, on_delete=models.PROTECT, related_name="+"
                    )
                }
                if self.link
                else {}
            ),
        }

    def get_meta_options(self, model: Any) -> dict[str, Any]:
        constraints = list(Statement._meta.constraints)
        for name, definition in sorted(self.aspects_for(model).items()):
            for suffix, columns, condition in (
                (
                    "current",
                    ("subject", "asserted_by", "aspect", *definition.members),
                    Q(aspect=name, superseded__isnull=True),
                ),
                (
                    "applied",
                    ("subject", "aspect", *definition.members),
                    Q(aspect=name, applied=True),
                ),
            ):
                constraints.append(
                    models.UniqueConstraint(
                        fields=columns,
                        condition=condition,
                        name=f"%(app_label)s_%(class)s_{name}_{suffix}",
                    )
                )
        return {
            "app_label": model._meta.app_label,
            "db_table": f"{model._meta.db_table}_{self.manager_name}",
            "base_manager_name": "objects",
            "constraints": constraints,
        }


class PairStatementManager(StatementManager):
    """Canonical ordering makes identity and keep-separate assertions symmetric."""

    other: Any

    def for_pair(self, left: models.Model, right: models.Model | None = None) -> Any:
        if right is None:
            if self.instance is None:
                raise ValidationError("A pair needs two persisted subjects.")
            left, right = self.instance, left
        if type(left) is not type(right) or left.pk is None or right.pk is None or left.pk == right.pk:
            raise ValidationError("A pair requires two distinct subjects of the same model.")
        if left._state.adding or right._state.adding or left._state.db != right._state.db:
            raise ValidationError("Pair subjects must be persisted on the same database.")
        first, second = sorted((left, right), key=lambda row: row.pk)
        manager = type(self)(self.model, first).db_manager(self._db)
        manager.other = second
        return manager

    def set(self, asserted_by: Any = None, *, other: Any = None, **kwargs: Any) -> Any:
        other = other if other is not None else getattr(self, "other", None)
        if other is None:
            raise ValidationError("Pair assertions require another subject.")
        pair = self.for_pair(other)
        alias = pair._write_alias()
        with transaction.atomic(using=alias):
            subjects = list(
                type(pair.instance)
                ._base_manager.using(alias)
                .select_for_update()
                .filter(pk__in=(pair.instance.pk, pair.other.pk))
                .order_by("pk")
            )
            if len(subjects) != 2:
                raise ValidationError("Both pair subjects must exist.")
            pair.instance, pair.other = subjects
            actor = current_actor()
            source = actor if asserted_by is None else asserted_by
            get_policy().authorize(actor, "change", pair.other)
            pair._source(source, alias, pair.other)
            kwargs.pop("aspect", None)
            return StatementManager.set(pair, asserted_by, aspect="same_as", other=pair.other, **kwargs)

    def confirm(self, *, values: Mapping[str, Any] | None = None, **kwargs: Any) -> Any:
        return self.set(rank="preferred", values=values or {}, **kwargs)

    def dismiss(self, **kwargs: Any) -> Any:
        return self.set(rank="preferred", snak="no_value", **kwargs)

    def kept_separate(self, left: Any, right: Any) -> bool:
        pair = self.for_pair(left, right)
        return bool(
            pair.get_queryset()
            .filter(other=pair.other, applied=True, snak="no_value", superseded__isnull=True)
            .exists()
        )


class PairAspect(StatementRecords):
    """Symmetric retained assertions; host actions alone decide whether to merge."""

    def __init__(self, *, link: str | None = None) -> None:
        super().__init__(
            aspects={"same_as": Aspect(member={"other": models.ForeignKey("self", on_delete=models.PROTECT)})},
            link=link,
        )
        self.history_manager = PairStatementManager
        self.custom_model_name = lambda name: f"{name}PairStatement"

    def get_extra_fields(self, model: Any, fields: Any) -> dict[str, Any]:
        return {**super().get_extra_fields(model, fields), "statement_pair": True}
