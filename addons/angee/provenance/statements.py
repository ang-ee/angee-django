"""Angee retained statements, source trust, permission admission, and replay.

These addon models compose native capture with Angee identity, document, merge,
and evidence contracts. The native capture modules have no dependency on them.
"""

from __future__ import annotations

import importlib
from collections import defaultdict
from collections.abc import Callable, Iterable, Mapping, Sequence
from copy import copy
from dataclasses import dataclass
from itertools import batched, groupby
from typing import Any, ClassVar, Self, cast

from django.core import checks
from django.core.exceptions import FieldDoesNotExist, ImproperlyConfigured, ValidationError
from django.core.validators import MaxValueValidator, MinValueValidator
from django.db import models, transaction
from django.db.models import Exists, F, OuterRef, Q
from django.db.models.query import ModelIterable
from django.utils import timezone
from rebac import (
    PermissionDenied,
    RebacMixin,
    SubjectRef,
    actor_context,
    current_actor,
    system_context,
    to_subject_ref,
)
from rebac.actors import is_sudo
from rebac.resources import model_for_subject_type, model_resource_type
from simple_history.manager import HistoryDescriptor
from simple_history.models import HistoricalRecords
from simple_history.utils import get_history_manager_for_model

from angee.base.fields.state import StateField
from angee.base.lineage import most_derived_concrete
from angee.base.managers import AngeeManager
from angee.base.models.identity import SqidMixin, TimestampMixin, update_fields_with_auto_now
from angee.base.models.locks import OptimisticLockMixin
from angee.base.models.merge_references import MergeContractMixin
from angee.base.models.trash import TrashMixin, TrashQuerySet
from angee.base.permissions import instance_actor, require_rebac_queryset
from angee.base.querysets import AngeeUnscopedQuerySet, SystemQuerysetMixin, by_key, query_batch_size
from angee.base.validators import validate_value
from angee.provenance.context import current_changeset
from angee.provenance.contrib.angee.history import HistoryDocumentMixin, HistoryMixin, ModelHistory
from angee.provenance.contrib.angee.querysets import AngeeHistoryQuerySet, AngeeHistoryUnscopedQuerySet
from angee.provenance.evidence import (
    EvidenceReference,
    FactAuthority,
    readable_evidence,
    readable_evidence_records,
    readable_records,
)
from angee.provenance.history import _ChangesetHistory


def _statement_column(model: type[models.Model], name: str) -> models.Field:
    """Resolve declared values through Django's forward field metadata."""

    field = model._meta.get_field(name)
    if not isinstance(field, models.Field):
        raise ImproperlyConfigured(f"{model._meta.label}.{name} must be a forward statement column.")
    return field


def _statement_foreign_key(model: type[models.Model], name: str) -> models.ForeignKey:
    field = _statement_column(model, name)
    if not isinstance(field, models.ForeignKey):
        raise ImproperlyConfigured(f"{model._meta.label}.{name} must be a statement foreign key.")
    return field


class StatementQuerySet[T: Statement](AngeeHistoryUnscopedQuerySet[T]):
    """Chainable assertion scopes; the manager owns subject admission."""

    _statement_reader: object | None = None

    def _clone(self) -> Self:
        clone = super()._clone()
        clone._statement_reader = getattr(self, "_statement_reader", None)
        return clone

    def _fetch_all(self) -> None:
        fresh = self._result_cache is None
        super()._fetch_all()
        reader = getattr(self, "_statement_reader", None)
        if fresh and reader is not None and self._result_cache and issubclass(self._iterable_class, ModelIterable):
            prime_readable_statement_records(self._result_cache, reader)

    def update(self, **kwargs: Any) -> int:
        """Attribute the rows to the writing changeset, as ``Statement.save`` does.

        A statement row's ``changeset`` is its last writer and its history copies
        it, so a queryset write that left it would attribute the change to an older
        changeset (and a revert of the writing changeset would not find it).
        """

        if "changeset" not in kwargs and "changeset_id" not in kwargs:
            kwargs["changeset"] = current_changeset(required=True, record=True)
        return super().update(**kwargs)

    def current(self) -> Self:
        return self.filter(superseded__isnull=True)

    def for_reader(self, actor: Any) -> Self:
        """Scope shadow rows through every record endpoint the reader may read.

        The scopes are applied eagerly (``scoped()``): a lazily scoped queryset
        embedded as a subquery compiles without the actor's scope. Nullable member
        columns belong to other aspects and do not withhold those rows.
        """

        if actor is None:
            return self.none()
        subjects = require_rebac_queryset(self.model.statement_subject_model).with_actor(actor).with_action("read")
        rows = self.filter(subject_id__in=models.Subquery(cast(Any, subjects).scoped().values("pk")))
        member_names = sorted({name for aspect in self.model.statement_aspects.values() for name in aspect.members})
        for name in member_names:
            field = _statement_column(self.model, name)
            if not isinstance(field, models.ForeignKey):
                continue
            related = field.remote_field.model
            if model_resource_type(related) is None:
                continue
            members = require_rebac_queryset(related).with_actor(actor).with_action("read").scoped()
            rows = rows.filter(
                Q(**{f"{field.attname}__isnull": True})
                | Q(**{f"{field.attname}__in": models.Subquery(members.values(field.target_field.name))})
            )
        rows._statement_reader = actor
        return rows

    def with_readable_records(self, actor: Any) -> Self:
        """Batch evidence and asserting-source admission for the evaluated page."""

        rows = self._chain()
        rows._statement_reader = actor
        return rows

    def applied(self) -> Self:
        """Standing value assertions selected by survivorship."""

        return self.current().filter(
            status=self.model.StatementStatus.ASSERTED, applied=True, snak=self.model.StatementSnak.VALUE
        )

    def provenance(self, field: str) -> Self:
        """Return the applied assertions, including a preferred dismissal."""

        aspects = [
            name for name, aspect in self.model.statement_aspects.items() if field in (*aspect.fields, *aspect.members)
        ]
        if not aspects:
            raise FieldDoesNotExist(f"No aspect owns {field!r}.")
        return self.current().filter(aspect__in=aspects, applied=True)

    def delete(self) -> Any:
        raise ValidationError("Statements are retained; withdraw them instead.")


def prime_readable_statement_records(rows: Sequence[Any], actor: Any) -> None:
    """Resolve a statement page's evidence and asserting principals by model."""

    evidence_by_row: list[tuple[Any, tuple[EvidenceReference, ...]]] = []
    refs: list[EvidenceReference] = []
    sources_by_model: dict[tuple[type[models.Model], str], set[str]] = defaultdict(set)
    sources_by_row: list[tuple[Any, tuple[type[models.Model], str, str] | None]] = []
    for row in rows:
        evidence = tuple(
            EvidenceReference.model_validate(value) for value in row.evidence[: Statement.EVIDENCE_LIMIT]
        )
        evidence_by_row.append((row, evidence))
        refs.extend(evidence)
        source = SubjectRef.parse(row.asserted_by)
        resolved = model_for_subject_type(source.subject_type)
        if resolved is None:
            sources_by_row.append((row, None))
            continue
        model, id_attr = resolved
        sources_by_model[(model, id_attr)].add(source.subject_id)
        sources_by_row.append((row, (model, id_attr, source.subject_id)))

    evidence_records = readable_evidence_records(refs, actor)
    readable_evidence = {
        (ref.model.lower(), ref.id, ref.pointer): record for ref, record in evidence_records
    }
    for row, evidence in evidence_by_row:
        row._readable_evidence = tuple(
            ref for ref in evidence if (ref.model.lower(), ref.id, ref.pointer) in readable_evidence
        )
        row._readable_evidence_records = tuple(
            (ref, readable_evidence[ref.model.lower(), ref.id, ref.pointer])
            for ref in evidence
            if (ref.model.lower(), ref.id, ref.pointer) in readable_evidence
        )

    readable_sources: dict[tuple[type[models.Model], str, str], models.Model] = {}
    for (model, id_attr), sources in sources_by_model.items():
        visible = (
            require_rebac_queryset(model).with_actor(actor)
            .with_action("read")
            .filter(**{f"{id_attr}__in": sources})
        )
        readable_sources.update({
            (model, id_attr, str(getattr(row, id_attr))): row for row in visible
        })
    for row, source_key in sources_by_row:
        row._asserting_source_readable = source_key in readable_sources
        row._readable_asserting_source = readable_sources.get(source_key) if source_key is not None else None


_StatementManagerBase = models.Manager.from_queryset(StatementQuerySet)


class StatementManager[T: Statement](_StatementManagerBase[T]):
    """Locked upserts and survivorship on the generated assertion model."""

    def __init__(self, model: type[T] | None = None, instance: models.Model | None = None) -> None:
        super().__init__()
        if model is not None:
            self.model = model
        self.instance = instance
        if instance is not None:
            self._db = instance._state.db

    def get_queryset(self) -> StatementQuerySet[T]:
        rows = cast(StatementQuerySet[T], super().get_queryset())
        return rows if self.instance is None else rows.filter(subject=self.instance)

    def set(
        self,
        *,
        aspect: str,
        values: Mapping[str, Any],
        asserted_by: Any = None,
        link: Any = None,
        evidence: Sequence[Any] = (),
        confidence: float = 1.0,
        rank: str | None = None,
        snak: str | None = None,
        basis_hash: str = "",
        **natural_key: Any,
    ) -> Any:
        """Lock the subject, admit the source, upsert its assertion, then resolve.

        Per-aspect partial keys use locked update_or_create. Equal assertions
        return without saving or creating a historical row.
        """
        declaration = self.model.statement_aspects[aspect]
        member_key = {name: natural_key.pop(name) for name in declaration.members if name in natural_key}
        if self.instance is not None:
            if natural_key:
                raise ValidationError("A bound statement manager already has its subject.")
            natural_key = {"pk": self.instance.pk}
        if not natural_key:
            raise ValidationError("Statements require a subject natural key.")
        with transaction.atomic(using=self.db):
            subject = self.model.statement_subject_model._base_manager.using(self.db).get(**natural_key)
            subject = most_derived_concrete(subject).lock_subject(member_key)
            source, members, defaults = self._admit(
                subject,
                declaration,
                member_key,
                values=values,
                asserted_by=asserted_by,
                link=link,
                evidence=evidence,
                confidence=confidence,
                rank=rank,
                snak=snak,
                basis_hash=basis_hash,
            )
            key = dict(subject=subject, asserted_by=str(source), aspect=aspect, superseded=None, **members)
            candidate = self.model(**key, **defaults)
            candidate.full_clean(validate_unique=False, validate_constraints=False)
            existing = self.model._base_manager.using(self.db).filter(**key).first()
            if existing is not None and existing.same_values(candidate, defaults):
                return existing
            with system_context(reason="statements.set"):
                statement, _ = self.model._base_manager.db_manager(self.db).update_or_create(**key, defaults=defaults)
                self.resolve(subject=subject, aspect=aspect, members=members)
            statement.refresh_from_db()
            return statement

    def asserting_source(self, asserted_by: Any = None) -> SubjectRef:
        """Resolve the asserting principal and require its actor or trusted delegation."""
        requester = instance_actor(self.instance) if self.instance is not None else current_actor()
        source = to_subject_ref(asserted_by if asserted_by is not None else requester)
        if source != requester and not is_sudo():
            raise PermissionDenied("Only the source may change its statement stance.")
        return source

    def _admit(
        self,
        subject: Any,
        declaration: Aspect,
        member_key: dict[str, Any],
        *,
        values: Mapping[str, Any],
        asserted_by: Any,
        link: Any,
        evidence: Sequence[Any],
        confidence: float,
        rank: str | None,
        snak: str | None,
        basis_hash: str,
    ) -> tuple[SubjectRef, dict[str, Any], dict[str, Any]]:
        source = self.asserting_source(asserted_by)
        kind = StatementSourceMixin.source_kind_for(source)
        if kind is None:
            raise ValidationError("The source must declare a known per-row source_kind.")
        rank = self.model.StatementRank.NORMAL if rank is None else rank
        snak = self.model.StatementSnak.VALUE if snak is None else snak
        if rank == self.model.StatementRank.PREFERRED and kind not in (
            SourceKind.PERSON,
            SourceKind.SEED,
        ):
            # A preferred statement is a standing decision: a person's, or seeded install
            # data's (a keep-separate recorded without a person, by ruling).
            raise PermissionDenied("Only person and seed sources may assert preferred statements.")
        subject.require_statement_write(source)
        if set(values) - set(declaration.fields) or (snak != self.model.StatementSnak.VALUE and values):
            raise ValidationError("Only value snaks carry the declared aspect's values.")
        refs = validate_value(list[EvidenceReference], list(evidence), field="evidence")
        if kind == SourceKind.PERSON:
            readable_records(refs, (source,))
        members = declaration.coerce_members(self.model, source, member_key, kind=SourceKind(kind))
        defaults = {name: values.get(name) for name in declaration.fields}
        defaults.update(
            snak=snak,
            confidence=confidence,
            rank=rank,
            status=self.model.StatementStatus.ASSERTED,
            source_kind=kind,
            evidence=[ref.model_dump(mode="json") for ref in refs],
            basis_hash=basis_hash,
        )
        if self.model.statement_link:
            if link is not None and not type(link).objects.with_actor(source).with_action("read").filter(
                pk=link.pk
            ).exists():
                raise PermissionDenied("The asserting source requires read on the link.")
            defaults["link"] = link
        elif link is not None:
            raise ValidationError("This statement declaration has no link model.")
        return source, members, defaults

    def _transition(
        self,
        status: Statement.StatementStatus,
        *,
        asserted_by: Any,
        subjects: Sequence[Any] | None = None,
        supported: Sequence[Mapping[str, Any]] | None = None,
        changeset: Any = None,
        aspect: str | None = None,
        link: Any = None,
    ) -> int:
        source = self.asserting_source(asserted_by)
        rows = self.model._base_manager.using(self.db).filter(asserted_by=str(source), superseded__isnull=True)
        rows = rows.exclude(status=status)
        if (bound := self.instance) is not None:
            rows = rows.filter(subject=bound)
        if aspect is not None:
            rows = rows.filter(aspect=aspect)
        if link is not None:
            rows = rows.filter(link=link)
        if changeset is not None:
            rows = rows.filter(changeset=changeset)
        ids = set(rows.values_list("pk", flat=True))
        if subjects is not None:
            selected: set[object] = set()
            for subject_batch in by_key(
                self.model.statement_subject_model._base_manager.using(self.db),
                [subject.pk if isinstance(subject, models.Model) else subject for subject in subjects],
            ):
                selected.update(rows.filter(subject__in=subject_batch.values("pk")).values_list("pk", flat=True))
            ids.intersection_update(selected)
        if supported is not None:
            for key in supported:
                if set(key) != {"subject", "aspect", *self.model.statement_aspects[key["aspect"]].members}:
                    raise ValidationError("Supported keys require subject, aspect and every member.")
            width = max((len(key) for key in supported), default=1)
            for supported_batch in batched(supported, query_batch_size(rows, width=width)):
                supported_filter = models.Q(pk__in=[])
                for key in supported_batch:
                    supported_filter |= models.Q(**key)
                ids.difference_update(rows.filter(supported_filter).values_list("pk", flat=True))
        affected: dict[Any, list[Any]] = {}
        for statement_batch in by_key(rows, sorted(ids)):
            for pk, subject_id in statement_batch.values_list("pk", "subject_id"):
                affected.setdefault(subject_id, []).append(pk)
        count = 0
        for subject_id, subject_keys in sorted(affected.items()):
            with transaction.atomic(using=self.db):
                subject = self.model.statement_subject_model._base_manager.using(self.db).get(pk=subject_id)
                subject = most_derived_concrete(subject).lock_subject()
                subject.require_statement_write(source)
                with system_context(reason="statements.transition"):
                    groups: dict[tuple[Any, ...], dict[str, Any]] = {}
                    for statement_batch in by_key(rows.filter(subject=subject), subject_keys):
                        for row in statement_batch.order_by("pk").select_for_update():
                            row.status = status
                            # A transition changes the source's stance, so it moves its recency.
                            row.save(using=self.db, update_fields=("status", "updated_at"))
                            members = {
                                name: _statement_column(type(row), name).value_from_object(row)
                                for name in self.model.statement_aspects[row.aspect].members
                            }
                            groups[row.aspect, *members.values()] = members
                            count += 1
                    for (changed_aspect, *_), members in groups.items():
                        self.resolve(subject=subject, aspect=changed_aspect, members=members)
        return count

    def withdraw(self, asserted_by: Any, subjects: Sequence[Any] | None = None, *, link: Any = None) -> int:
        return self._transition(
            self.model.StatementStatus.WITHDRAWN, asserted_by=asserted_by, subjects=subjects, link=link
        )

    def withdraw_unsupported(
        self, asserted_by: Any, supported: Sequence[Mapping[str, Any]], *, aspect: str | None = None, link: Any = None
    ) -> int:
        return self._transition(
            self.model.StatementStatus.WITHDRAWN, asserted_by=asserted_by, supported=supported, aspect=aspect, link=link
        )

    def resolve(
        self, *, subject: Any = None, aspect: str | None = None, members: Mapping[str, Any] | None = None
    ) -> None:
        """Resolve each field group atomically; normal unknowns never erase values."""
        subject = subject if subject is not None else self.instance
        if subject is None:
            raise ValidationError("Survivorship requires one subject.")
        subject = most_derived_concrete(subject)
        actor = instance_actor(subject)
        if not is_sudo():
            subject.require_statement_write(actor)
        using = subject._state.db
        with transaction.atomic(using=using), system_context(reason="statements.resolve"):
            subject = subject.lock_subject(members or {})
            rows = self.model._base_manager.using(using).filter(subject=subject, superseded__isnull=True)
            if aspect is not None:
                rows = rows.filter(aspect=aspect, **(members or {}))
            link_field = self.model.link_field()
            if link_field is not None:
                link_model = link_field.remote_field.model
                if not issubclass(link_model, StatementLinkMixin):
                    raise ImproperlyConfigured("Statement link models require StatementLinkMixin.")
                rows = rows.select_related("link", *(f"link__{name}" for name in link_model.authority_related))
            member_names = sorted(
                {name for declaration in self.model.statement_aspects.values() for name in declaration.members}
            )

            def group(row: Any) -> tuple[Any, ...]:
                return (
                    row.aspect,
                    *(
                        _statement_column(type(row), name).value_from_object(row)
                        for name in self.model.statement_aspects[row.aspect].members
                    ),
                )

            for key, grouped in groupby(rows.order_by("aspect", *member_names), key=group):
                candidates = list(grouped)
                declaration = self.model.statement_aspects[key[0]]
                asserted = [row for row in candidates if row.status == self.model.StatementStatus.ASSERTED]
                preferred = sorted(
                    (row for row in asserted if row.rank == self.model.StatementRank.PREFERRED),
                    key=lambda row: row.sort_key(declaration),
                )
                normal: Sequence[Any] = [row for row in asserted if row.rank == self.model.StatementRank.NORMAL]
                if declaration.candidates is not None:
                    normal = declaration.candidates(subject, normal)
                if any(row not in asserted or row.rank != self.model.StatementRank.NORMAL for row in normal):
                    raise ValidationError("Candidate policy may only select asserted normal statements.")
                ordered = sorted(normal, key=lambda row: row.sort_key(declaration))
                winner = (
                    preferred[0]
                    if preferred
                    else next((row for row in ordered if row.snak == self.model.StatementSnak.VALUE), None)
                )
                values = {
                    name: _statement_column(type(winner), name).value_from_object(winner)
                    if winner is not None and winner.snak == self.model.StatementSnak.VALUE
                    else None
                    for name in declaration.fields
                }
                if winner is not None and winner.snak == self.model.StatementSnak.VALUE and declaration.allow_fill:
                    for name, value in values.items():
                        if value in (None, ""):
                            values[name] = next(
                                (
                                    _statement_column(type(row), name).value_from_object(row)
                                    for row in ordered
                                    if row.snak == self.model.StatementSnak.VALUE
                                    and _statement_column(type(row), name).value_from_object(row) not in (None, "")
                                ),
                                value,
                            )
                for row in sorted(candidates, key=lambda row: row is winner):
                    if row.applied != (row is winner):
                        row.applied = row is winner
                        row.save(using=using, update_fields=("applied",))
                if (winner is not None and winner.snak == self.model.StatementSnak.SOME_VALUE) or (
                    winner is None and any(row.snak == self.model.StatementSnak.SOME_VALUE for row in asserted)
                ):
                    continue
                subject.apply_resolved(
                    aspect=key[0],
                    member=dict(zip(declaration.members, key[1:], strict=True)),
                    values=values,
                    statement=winner,
                    expected_revision=subject.statement_revision,
                )

    def merge(self, reference: Any, rows: models.QuerySet[Any], *, survivor: Any) -> None:
        """Retain both assertions on key collisions, then resolve the moved subject.

        A move re-keys a stance without changing it, so each row keeps its
        recency (``updated_at``) and the trust ordering ranks it as before.
        """
        target = reference.lookup(survivor) if reference is not None else {"subject_id": survivor.pk}
        columns = reference.columns if reference is not None else ("subject",)
        subjects = set()
        if self.model.statement_link:
            rows = rows.select_related("link")
        for row in rows.order_by("pk"):
            if row.superseded_id is None:
                key = dict(row.current_key(), superseded__isnull=True)
                key.update(target)
                kept = self.model._base_manager.using(rows.db).filter(**key).exclude(pk=row.pk).first()
                if kept is not None:
                    winner = min((kept, row), key=lambda item: item.sort_key())
                    loser = row if winner is kept else kept
                    loser.superseded = winner
                    loser.applied = False
                    loser.save(using=rows.db, update_fields=("superseded", "applied"))
            for name, value in target.items():
                setattr(row, name, value)
            # Applied rows from different sources also share the destination's key.
            row.applied = False
            row.save(using=rows.db, update_fields=(*columns, "applied"))
            subjects.add(row.subject_id)
        for subject_id in sorted(subjects):
            subject = self.model.statement_subject_model._base_manager.using(rows.db).get(pk=subject_id)
            self.db_manager(rows.db).resolve(subject=subject)


_StatementPairManagerBase = models.Manager.from_queryset(AngeeUnscopedQuerySet)


class StatementPairManager[T: StatementPair](_StatementPairManagerBase[T]):
    """Bind ordered carriers to an endpoint; endpoints own pair admission."""

    def __init__(self, model: type[T] | None = None, instance: models.Model | None = None) -> None:
        super().__init__()
        if model is not None:
            self.model = model
        self.instance = instance
        if instance is not None:
            self._db = instance._state.db

    def get_queryset(self) -> AngeeUnscopedQuerySet[T]:
        rows = cast(AngeeUnscopedQuerySet[T], super().get_queryset())
        return (
            rows if self.instance is None else rows.filter(models.Q(left=self.instance) | models.Q(right=self.instance))
        )

    def kept_separate(self, left: Any, right: Any) -> bool:
        """Whether an applied keep-separate decision (a preferred no-value) stands between the two."""

        first, second = sorted((left.pk, right.pk))
        statements = self.model.statements
        return statements.using(left._state.db).current().filter(
            subject__left_id=first, subject__right_id=second, aspect=self.model.pair_name,
            applied=True, snak=statements.model.StatementSnak.NO_VALUE,
        ).exists()

    def for_pair(self, left: Any, right: Any) -> Any:
        model = _statement_foreign_key(self.model, "left").remote_field.model
        if not all(isinstance(row, model) and row.pk is not None for row in (left, right)) or left.pk == right.pk:
            raise ValidationError("A pair requires two distinct persisted endpoints of its declared model.")
        using = left._state.db
        if right._state.db != using:
            raise ValidationError("Pair endpoints must belong to the same database.")
        actor = instance_actor(left)
        for endpoint in (left, right):
            visible = require_rebac_queryset(model).with_actor(actor).with_action("read").using(using)
            if not visible.filter(pk=endpoint.pk).exists():
                raise PermissionDenied("A pair requires read on both endpoints.")
        ordered = sorted((left, right), key=lambda endpoint: endpoint.pk)
        with transaction.atomic(using=using):
            for endpoint in ordered:
                endpoint.lock_subject()
            pair, _ = self.model._base_manager.using(using).get_or_create(
                left_id=ordered[0].pk, right_id=ordered[1].pk,
            )
            pair.with_actor(actor)
            if pair.is_trashed:
                pair.require_statement_write(actor)
                with system_context(reason="statements.pair.restore"):
                    pair.restore()
        return pair

    def merge(self, reference: Any, rows: Any, *, survivor: Any) -> None:
        """Re-key third-party decisions; keep the merged endpoints as evidence."""

        for pair in rows.untrashed().order_by("pk"):
            other = pair.right if "left" in reference.columns else pair.left
            if other.pk == survivor.pk:
                continue
            target = self.db_manager(rows.db).for_pair(survivor, other)
            self.model.statements.db_manager(rows.db).merge(None, pair.statements.all(), survivor=target)
            pair.trash(reason="Merged pair endpoints")


class StatementSubjectQuerySet[T: models.Model](TrashQuerySet[T], AngeeHistoryQuerySet[T]):
    """Canonical asserted rows, retaining withdrawn subjects and their identities."""

    def canonical(self) -> Self:
        statement_model = getattr(self.model, "statements").model
        statements = statement_model._base_manager.filter(subject_id=OuterRef("pk"))
        remaining = statements.exclude(status=statement_model.StatementStatus.WITHDRAWN)
        return self.untrashed().filter(~Exists(statements) | Exists(remaining))


_StatementSubjectManagerBase = AngeeManager.from_queryset(StatementSubjectQuerySet)


class StatementSubjectManager[T: models.Model](_StatementSubjectManagerBase[T]):
    """Shared scope for canonical models whose presence is asserted."""

class SourceKind(models.TextChoices):
    """Source classification, never a global precedence order."""

    PERSON = "person", "Person"
    SYSTEM_OF_RECORD = "system_of_record", "System of record"
    DIRECTORY = "directory", "Directory"
    NETWORK = "network", "Network"
    IMPORT = "import", "Import"
    SEED = "seed", "Seed"
    DOCUMENT = "document", "Document"
    EXTRACTION = "extraction", "Extraction"
    RULE = "rule", "Rule"


class StatementSourceMixin(models.Model):
    """Principal contract for assertion-time, per-row source classification.

    Principals compose this mixin and implement ``source_kind`` as their own
    property, field or constant. Unknown principals cannot assert statements.
    """

    class Meta:
        abstract = True

    @property
    def source_kind(self) -> SourceKind | None:
        raise ImproperlyConfigured("Statement principals must implement source_kind.")

    @classmethod
    def source_kind_for(cls, subject: Any) -> SourceKind | None:
        """Classify a persisted principal through its declared model contract."""

        source = to_subject_ref(subject)
        resolved = model_for_subject_type(source.subject_type)
        if resolved is None or source.optional_relation or source.subject_id == "*":
            return None
        model, id_attr = resolved
        if not issubclass(model, cls):
            return None
        try:
            principal = model._base_manager.get(**{id_attr: source.subject_id})
            kind = principal.source_kind
            return SourceKind(kind) if kind is not None else None
        except (model.DoesNotExist, TypeError, ValueError, ImproperlyConfigured, ValidationError):
            return None

class StatementLinkMixin(models.Model):
    """Contract of the model a ``StatementRecords(link=…)`` declaration names.

    The link carries the stream fact survivorship reads: ``authoritative`` says
    whether the asserting system-of-record stream outranks people for the aspects
    that list its kind. ``authority_related`` names the relations ``resolve()``
    preloads to read it without a query per statement.
    """

    authority_related: ClassVar[tuple[str, ...]] = ()

    class Meta:
        abstract = True

    @property
    def authoritative(self) -> bool:
        return False


@dataclass(frozen=True)
class Aspect:
    """One field group and its optional typed membership identity.

    ``member`` reuses a canonical field name or maps names to new Django fields.
    Shared members reuse one field. ``allow_fill`` fills missing winner values.
    """

    fields: tuple[str, ...] = ()
    member: str | Mapping[str, models.Field] | None = None
    precedence: tuple[SourceKind, ...] = ()
    allow_fill: bool = False
    candidates: Callable[[Any, Sequence[Any]], Sequence[Any]] | None = None
    apply: Callable[..., None] | None = None

    @property
    def members(self) -> tuple[str, ...]:
        """Return deterministic member-column names for the aspect's key."""

        if isinstance(self.member, str):
            return (self.member,)
        return tuple(sorted(self.member or ()))

    def priority(self, row: Any) -> int:
        """Place the row's source in this precedence; unlisted kinds sort last.

        A statement from an ``authoritative`` system-of-record stream precedes
        PERSON wherever this aspect lists both kinds: the stream is the system of
        record for the aspect, a plain stream is a system of reference below
        people. Every row is placed in one order, so rows of one group compare:
        where the precedence lists the system of record after PERSON, that order has
        an authoritative system-of-record place just before PERSON. Preferred rank is
        decided before this, so a person's confirm or dismiss stays on top.
        """

        kinds = self.precedence
        index = kinds.index(row.source_kind) if row.source_kind in kinds else len(kinds)
        if SourceKind.PERSON not in kinds or SourceKind.SYSTEM_OF_RECORD not in kinds:
            return index
        person = kinds.index(SourceKind.PERSON)
        if kinds.index(SourceKind.SYSTEM_OF_RECORD) < person:
            return index
        if row.source_kind == SourceKind.SYSTEM_OF_RECORD and row.authoritative:
            return person
        return index + 1 if index >= person else index

    def coerce_members(
        self, model: type[models.Model], source: SubjectRef, key: dict[str, Any], *, kind: SourceKind
    ) -> dict[str, Any]:
        """Consume this aspect's complete identity through native field ownership."""

        members = {name: key.pop(name) for name in self.members if name in key}
        if set(members) != set(self.members) or any(value is None for value in members.values()):
            raise ValidationError("Every aspect member is required.")
        for name, value in members.items():
            field = _statement_column(model, name)
            if isinstance(field, models.ForeignKey):
                pk = field.target_field.value_from_object(value) if isinstance(value, models.Model) else value
                rows = (
                    require_rebac_queryset(field.remote_field.model).with_actor(source).with_action("read")
                    if kind == SourceKind.PERSON and not is_sudo()
                    else field.remote_field.model._base_manager.all()
                )
                members[name] = rows.get(**{field.target_field.name: pk})
            else:
                members[name] = field.to_python(value)
        return members


class StatementsMixin(SystemQuerysetMixin, HistoryDocumentMixin):
    """Subject-owned eligibility and application hooks for typed statements."""

    class Meta:
        abstract = True

    @classmethod
    def history_related_columns(cls) -> dict[type[models.Model], tuple[str, ...]]:
        """Include pair endpoint identities without making participation imply capture."""

        columns = super().history_related_columns()
        for pair in cls.pair_models():
            columns[pair] = (*columns.get(pair, ()), "left_id", "right_id")
        return columns

    @classmethod
    def history_related_scopes(
        cls, predicate: Q, root_pk: Any, *, direct: bool, using: str | None,
    ) -> dict[type[models.Model], Q]:
        """Contribute assertions for direct subjects or the document's retained owned lines."""

        scopes = super().history_related_scopes(predicate, root_pk, direct=direct, using=using)
        subjects = get_history_manager_for_model(cls).using(using).filter(predicate).values(cls._meta.pk.attname)
        subject_scope = Q(subject_id=root_pk) if direct else Q(subject_id__in=models.Subquery(subjects))
        for statement in cls.statement_models():
            scopes[statement] = scopes[statement] | subject_scope if statement in scopes else subject_scope
        return scopes

    @classmethod
    def statement_models(cls) -> Iterable[type[Statement]]:
        """Yield the declared statement tables once, including MTI parents."""

        seen = set()
        for owner in (*reversed(cls._meta.get_parent_list()), cls):
            declaration = getattr(owner, "statements", None)
            if declaration is not None and declaration.model not in seen:
                seen.add(declaration.model)
                yield declaration.model

    @classmethod
    def pair_models(cls) -> tuple[type[StatementPair], ...]:
        """Read generated pair carriers from Django's declared endpoint relations."""

        carriers = {
            relation.related_model
            for relation in cls._meta.get_fields(include_hidden=True)
            if relation.auto_created and relation.one_to_many
            and relation.related_model is not None and issubclass(relation.related_model, StatementPair)
        }
        return tuple(sorted(carriers, key=lambda model: model._meta.label_lower))

    def statement_managers(self) -> Iterable[Any]:
        """Yield each MTI owner's bound table once, including parent declarations."""

        for model in self.statement_models():
            subject_model = model.statement_subject_model
            subject = (
                self
                if subject_model is type(self)
                else subject_model._base_manager.db_manager(self._state.db).get(pk=self.pk)
            )
            if subject is not self and (actor := instance_actor(self)) is not None:
                if not isinstance(subject, RebacMixin):
                    raise ImproperlyConfigured("Statement subjects require REBAC actor binding.")
                subject.with_actor(actor)
            yield getattr(subject, "statements")

    def statements_for(self, aspect: str) -> Any:
        """Return the single table declaring this aspect; conflicting owners refuse."""

        owners = [manager for manager in self.statement_managers() if aspect in manager.model.statement_aspects]
        if len(owners) != 1:
            raise ImproperlyConfigured(f"Expected one statement owner for {aspect!r}.")
        return owners[0]

    def revert_subjects(self) -> list[Any]:
        """A restored subject re-derives its aspects after the whole replay."""

        return [self]

    def save(self, *args: Any, **kwargs: Any) -> None:
        """Ordinary person edits assert normal statements; resolved saves stay ordinary.

        An aspect whose declared precedence leaves PERSON out is not a person's to
        state: its fields belong to the native owner, so a person's save writes them
        as an ordinary save and records no statement that later resolution could
        restore over the owner's writes. A create states no group whose values are
        all empty: an empty field on insert is the absence of a fact, so it never
        outranks a later source. Explicit confirm and dismiss stay available.
        """

        if is_sudo():
            super().save(*args, **kwargs)
            return
        actor = instance_actor(self)
        if actor is None or StatementSourceMixin.source_kind_for(actor) != SourceKind.PERSON:
            super().save(*args, **kwargs)
            return
        using = type(self)._base_manager.db_manager(hints={"instance": self}).db
        update_fields = kwargs.get("update_fields")
        saved_fields = (
            None
            if update_fields is None
            else {
                field.name
                for field in self._meta.concrete_fields
                if field.name in update_fields or field.attname in update_fields
            }
        )
        with actor_context(actor), transaction.atomic(using=using):
            previous = type(self)._base_manager.using(using).filter(pk=self.pk).first() if self.pk else None
            super().save(*args, **kwargs)
            resolved_fields: set[str] = set()
            for manager in self.statement_managers():
                for aspect, declaration in manager.model.statement_aspects.items():
                    fields = declaration.fields
                    if (
                        declaration.members
                        or not fields
                        or (declaration.precedence and SourceKind.PERSON not in declaration.precedence)
                        or not any(
                            (saved_fields is None or name in saved_fields)
                            and (previous is None or getattr(previous, name) != getattr(self, name))
                            for name in fields
                        )
                    ):
                        continue
                    values = {
                        name: getattr(self if saved_fields is None or name in saved_fields else previous, name)
                        for name in fields
                    }
                    if previous is None and all(value in (None, "") for value in values.values()):
                        # A field left empty on create is no fact; a later edit that clears one is.
                        continue
                    manager.set(aspect=aspect, values=values, asserted_by=actor)
                    resolved_fields.update(fields if saved_fields is None else set(fields) & saved_fields)
            if resolved_fields:
                self.refresh_from_db(using=using, fields=sorted(resolved_fields))

    def require_statement_write(self, actor: SubjectRef | None) -> None:
        """Admit assertions through the subject's native REBAC write scope."""

        rows = require_rebac_queryset(type(self)).filter(pk=self.pk).with_actor(actor).with_action("write")
        if not rows.exists():
            raise PermissionDenied("The asserting source requires write on the subject.")

    def apply_resolved(
        self,
        *,
        aspect: str,
        member: Mapping[str, Any],
        values: Mapping[str, Any],
        statement: Any | None,
        expected_revision: int | None = None,
    ) -> None:
        declaration = self.statements_for(aspect).model.statement_aspects[aspect]
        if declaration.apply is not None:
            declaration.apply(self, member=member, values=values, statement=statement)
            return
        # No statement or an unknown value retains the operational values; a
        # winning no-value (a person's dismissal) clears them so removal sticks.
        if statement is None or statement.snak == statement.StatementSnak.SOME_VALUE:
            return
        if member:
            raise ImproperlyConfigured("Member aspects require the subject's apply_resolved hook.")
        changed = []
        for name, value in values.items():
            field = _statement_column(type(self), name)
            value = field.get_default() if value is None and not field.null else value
            if getattr(self, field.attname) != value:
                setattr(self, field.attname, value)
                changed.append(name)
        if changed:
            kwargs: dict[str, Any] = {"update_fields": update_fields_with_auto_now(self, changed)}
            if isinstance(self, OptimisticLockMixin):
                kwargs["expected_revision"] = expected_revision
            self.save(**kwargs)

    @property
    def statement_revision(self) -> int | None:
        return getattr(self, self.REVISION_FIELD) if isinstance(self, OptimisticLockMixin) else None

    def provenance(self, field: str) -> Any:
        for manager in self.statement_managers():
            if any(field in (*aspect.fields, *aspect.members) for aspect in manager.model.statement_aspects.values()):
                return manager.provenance(field)
        raise FieldDoesNotExist(f"No aspect owns {field!r}.")

    def confirm(self, *, aspect: str, values: Mapping[str, Any], **members: Any) -> Any:
        manager = self.statements_for(aspect)
        return manager.set(
            aspect=aspect,
            values=values,
            rank=manager.model.StatementRank.PREFERRED,
            **members,
        )

    def dismiss(self, *, aspect: str, **members: Any) -> Any:
        manager = self.statements_for(aspect)
        return manager.set(
            aspect=aspect,
            values={},
            rank=manager.model.StatementRank.PREFERRED,
            snak=manager.model.StatementSnak.NO_VALUE,
            **members,
        )

    def apply_pair(self, *, aspect: str, other: Any, statement: Any | None) -> None:
        raise ImproperlyConfigured("Pair aspects require the endpoint's apply_pair hook.")


class StatementHistory(ModelHistory):
    """Retained stance replay and subject admission on statement history."""

    def subjects(self, first: Any, last: Any, previous: Any) -> Iterable[Any]:
        model = type(first.instance).statement_subject_model
        ids = sorted({version.subject_id for version in (first, last, previous) if version is not None})
        for pk in ids:
            subject = model._base_manager.using(first._state.db).filter(pk=pk).first()
            if subject is None:
                raise ValidationError(f"{model._meta.label} {pk}: a retained subject is missing.")
            yield subject

    def authorize_revert(self, first: Any, last: Any, previous: Any, actor: Any) -> None:
        if not is_sudo():
            for subject in self.subjects(first, last, previous):
                subject.require_statement_write(to_subject_ref(actor))

    def revert_order(self, first: Any, last: Any, previous: Any) -> tuple[Any, ...]:
        return (1, previous is None, first._meta.label_lower, first.id)

    def stage_revert(self, first: Any, last: Any, previous: Any) -> None:
        """Release applied uniqueness and stage collisions against a retained peer.

        A real row sharing the current key temporarily holds supersession;
        replay restores the exact predecessor stance within this transaction.
        """

        rows = type(first.instance)._base_manager.using(first._state.db)
        row = rows.filter(pk=first.id).first()
        if row is None:
            raise ValidationError(f"{first._meta.label} {first.id}: a retained statement is missing.")
        if row.superseded_id is None:
            row.superseded = rows.filter(**row.current_key()).exclude(pk=row.pk).first()
        row.applied = False
        row.save(using=first._state.db, update_fields=("superseded", "applied"))

    def revert_row(self, first: Any, last: Any, previous: Any) -> list[Any]:
        """Restore the stance before the changeset, recency included; a first assertion withdraws.

        ``updated_at`` is the stance's recency in :meth:`Statement.sort_key`, so a
        restored stance keeps the recency it had: stamping the revert's time would
        let a reverted stance outrank the stance it lost to before the changeset.
        A withdrawal is a status transition and takes the revert's time, as
        :meth:`StatementManager._transition` does. One captured queryset update
        writes the row, since a save stamps ``auto_now`` columns.
        """
        row = (previous or first).instance
        model = type(row)
        using = first._state.db
        values = {
            field.attname: getattr(row, field.attname)
            for field in model._meta.concrete_fields
            if not field.primary_key
        }
        values.pop("changeset_id", None)  # the update attributes the writing changeset
        values.update(applied=False)
        if previous is None:
            values.update(
                status=row.StatementStatus.WITHDRAWN,
                updated_at=timezone.now(),
                superseded_id=model._base_manager.using(using)
                .filter(**row.current_key(), superseded__isnull=True)
                .exclude(pk=row.pk)
                .values_list("pk", flat=True)
                .first(),
            )
        model._base_manager.using(using).filter(pk=row.pk).update(**values)
        return list(self.subjects(first, last, previous))


class StatementPairHistory(ModelHistory):
    """Pair replay is admitted through both canonical endpoints."""

    def authorize_revert(self, first: Any, last: Any, previous: Any, actor: Any) -> None:
        if not is_sudo():
            (previous or last).instance.require_statement_write(to_subject_ref(actor))


class Statement(MergeContractMixin, HistoryMixin, _ChangesetHistory, SqidMixin, TimestampMixin, RebacMixin):
    """Retained shadow row; its canonical subject owns admission and field gates."""

    class StatementSnak(models.TextChoices):
        VALUE = "value"
        SOME_VALUE = "some_value"
        NO_VALUE = "no_value"

    class StatementRank(models.TextChoices):
        PREFERRED = "preferred"
        NORMAL = "normal"

    class StatementStatus(models.TextChoices):
        ASSERTED = "asserted"
        WITHDRAWN = "withdrawn"
        QUARANTINED = "quarantined"
        DEPRECATED = "deprecated"

    history = StatementHistory(inherit=True)
    objects = StatementManager()
    _angee_base = StatementManager()
    statement_subject_model: type[StatementsMixin]
    statement_aspects: dict[str, Aspect]
    statement_link: str | None
    subject: models.ForeignKey[Any, Any]
    asserted_by = models.TextField()
    source_kind = StateField(choices_enum=SourceKind)
    """Retained assertion-time classification, independent of later principal edits."""
    aspect = models.CharField(max_length=64)
    snak = StateField(choices_enum=StatementSnak, default=StatementSnak.VALUE)
    confidence = models.FloatField(default=1.0, validators=(MinValueValidator(0), MaxValueValidator(1)))
    rank = StateField(choices_enum=StatementRank, default=StatementRank.NORMAL)
    status = StateField(choices_enum=StatementStatus, default=StatementStatus.ASSERTED)
    applied = models.BooleanField(default=False)
    evidence = models.JSONField(default=list, blank=True)
    basis_hash = models.CharField(max_length=64, blank=True)
    superseded = models.ForeignKey("self", null=True, blank=True, on_delete=models.PROTECT, related_name="+")

    class Meta:
        abstract = True
        base_manager_name = "_angee_base"
        rebac_resource_type = None

    @classmethod
    def stance_constraint(cls) -> models.CheckConstraint:
        return models.CheckConstraint(
            condition=Q(confidence__gte=0, confidence__lte=1)
            & (
                Q(rank=cls.StatementRank.NORMAL)
                | Q(rank=cls.StatementRank.PREFERRED, source_kind__in=[SourceKind.PERSON, SourceKind.SEED])
            )
            & Q(
                snak__in=cls.StatementSnak.values,
                status__in=cls.StatementStatus.values,
                source_kind__in=SourceKind.values,
            ),
            name="%(app_label)s_%(class)s_stance",
        )

    @classmethod
    def valid_column(cls, name: str, field: Any) -> bool:
        """The generator omits invalid columns; check() reports their declarations."""

        reserved = {field.name for field in Statement._meta.fields} | {"subject", "link", "id", "pk", "history"}
        return (
            name not in reserved
            and name.isidentifier()
            and isinstance(field, models.Field)
            and not field.primary_key
            and not field.many_to_many
            and not isinstance(field, models.GeneratedField)
            and not (isinstance(field, models.ForeignKey) and field.remote_field.parent_link)
        )

    @classmethod
    def link_field(cls) -> models.ForeignKey[Any, Any] | None:
        """Return the declared optional link's native field when enabled."""

        return _statement_foreign_key(cls, "link") if cls.statement_link else None

    @classmethod
    def check(cls, **kwargs: Any) -> list[checks.CheckMessage]:
        """Validate declarations after all subject and relation models are prepared."""

        errors = super().check(**kwargs)
        if cls._meta.abstract:
            return errors

        def error(message: str, code: str) -> None:
            errors.append(checks.Error(message, obj=cls, id=f"provenance.{code}"))

        if not issubclass(cls.statement_subject_model, StatementsMixin) or not cls.statement_aspects:
            error("StatementRecords requires StatementsMixin and at least one aspect.", "E039")
        link_field = cls.link_field()
        if link_field is not None:
            link_model = link_field.remote_field.model
            if not isinstance(link_model, type) or not issubclass(link_model, StatementLinkMixin):
                error(f"Statement link model {cls.statement_link!r} must compose StatementLinkMixin.", "E044")
        claimed: set[str] = set()
        member_fields: dict[str, Any] = {}
        for key, aspect in sorted(cls.statement_aspects.items()):
            if not key or len(key) > 64 or not isinstance(aspect, Aspect):
                error(f"Invalid or conflicting aspect {key!r}; use a key of at most 64 characters.", "E040")
                continue
            if claimed.intersection(aspect.fields) or len(set(aspect.fields)) != len(aspect.fields):
                error("A value field belongs to exactly one aspect.", "E041")
            claimed.update(aspect.fields)
            if (
                len(set(aspect.precedence)) != len(aspect.precedence)
                or any(kind not in SourceKind.values for kind in aspect.precedence)
            ):
                error(f"Invalid source precedence on {key!r}.", "E042")
            columns = {name: cls.statement_subject_model._meta.get_field(name) for name in aspect.fields}
            members = (
                aspect.member
                if isinstance(aspect.member, Mapping)
                else {name: cls.statement_subject_model._meta.get_field(name) for name in aspect.members}
            )
            for name, field in {**columns, **members}.items():
                if not cls.valid_column(name, field):
                    error(f"Invalid statement column {name!r}.", "E043")
                try:
                    cls._meta.get_field(name)
                except FieldDoesNotExist:
                    error(f"Aspect {key!r} claims excluded column {name!r}.", "E043")
            for name, field in members.items():
                if name in member_fields and member_fields[name] is not field:
                    error(f"Member {name!r} must reuse the same field.", "E041")
                member_fields[name] = field
        if claimed.intersection(member_fields):
            error("A member cannot also be a value column.", "E041")
        return errors

    trust_ordering: ClassVar[tuple[str, ...]] = ("-rank", "-confidence", "-updated_at", "-pk")
    """SQL display order of stances (after ``-applied``); precedence is not a column, see :meth:`sort_key`."""

    EVIDENCE_LIMIT: ClassVar[int] = 20

    def sort_key(self, aspect: Aspect | None = None) -> tuple[Any, ...]:
        """The one trust ordering: preferred rank, source precedence, confidence, ties, recency, identity.

        Survivorship, cross-subject member resolution and merge collapse all sort
        by this key.
        Recency is ``updated_at``, when the source last changed its stance
        (:meth:`save`).
        """

        declaration = self.statement_aspects[self.aspect] if aspect is None else aspect
        return (
            self.rank != self.StatementRank.PREFERRED,
            declaration.priority(self),
            -self.confidence,
            *self.tie_break(),
            -self.updated_at.timestamp(),
            -self.pk,
        )

    def tie_break(self) -> tuple[Any, ...]:
        """Domain tie-breaks between equally trusted stances, before recency."""

        return ()

    @property
    def authoritative(self) -> bool:
        """Whether the asserting stream is the system of record for this statement's aspects."""

        field = self.link_field()
        if field is None:
            return False
        link = field.value_from_object(self)
        if link is None:
            return False
        linked = getattr(self, field.name)
        if not isinstance(linked, StatementLinkMixin):
            raise ImproperlyConfigured("Statement link models require StatementLinkMixin.")
        return linked.authoritative

    @property
    def fact_authority(self) -> FactAuthority:
        """Classify this applied fact with the shared decision-context vocabulary."""

        if self.source_kind == SourceKind.PERSON:
            return FactAuthority.CORRECTION
        if self.source_kind in (SourceKind.EXTRACTION, SourceKind.RULE):
            return FactAuthority.UNVERIFIED
        return FactAuthority.SOURCE

    @property
    def readable_asserting_source(self) -> SubjectRef | None:
        """Return the asserting principal only when ``for_reader`` admitted it."""

        return SubjectRef.parse(self.asserted_by) if self.__dict__.get("_asserting_source_readable") else None

    @property
    def is_confirmed(self) -> bool:
        return self.rank == self.StatementRank.PREFERRED and self.snak == self.StatementSnak.VALUE

    @property
    def is_dismissed(self) -> bool:
        return self.rank == self.StatementRank.PREFERRED and self.snak == self.StatementSnak.NO_VALUE

    def member_values(self) -> dict[str, Any]:
        """This stance's member identity, as the subject verbs take it."""

        return {name: getattr(self, name) for name in self.statement_aspects[self.aspect].members}

    def confirm(self) -> Any:
        """Write the acting person's preferred statement for this stance's values."""

        values = {name: getattr(self, name) for name in self.statement_aspects[self.aspect].fields}
        return self.subject.with_actor(instance_actor(self)).confirm(
            aspect=self.aspect, values=values, **self.member_values()
        )

    def dismiss(self) -> Any:
        """Write the acting person's preferred no-value statement for this stance."""

        return self.subject.with_actor(instance_actor(self)).dismiss(aspect=self.aspect, **self.member_values())

    @property
    def evidence_truncated(self) -> bool:
        return len(self.evidence) > self.EVIDENCE_LIMIT

    def readable_evidence(self, actor: Any) -> list[Any]:
        """The bounded origins ``actor`` may read; each origin keeps its own read gate."""

        self.subject.require_access("read", actor)
        refs = [EvidenceReference.model_validate(value) for value in self.evidence[: self.EVIDENCE_LIMIT]]
        return readable_evidence(refs, actor)

    def same_values(self, other: Statement, names: Iterable[str]) -> bool:
        return all(
            _statement_column(type(self), name).value_from_object(self)
            == _statement_column(type(self), name).value_from_object(other)
            for name in names
        )

    def current_key(self) -> dict[str, Any]:
        """The aspect's identity, shared by merge collapse and its inverse."""

        fields = ("subject", "asserted_by", "aspect", *self.statement_aspects[self.aspect].members)
        return {
            _statement_column(type(self), name).attname: _statement_column(type(self), name).value_from_object(self)
            for name in fields
        }

    def save(self, *args: Any, **kwargs: Any) -> None:
        """Record the writing changeset; a partial save writes only the columns it names.

        ``updated_at`` is the stance's recency in :meth:`sort_key`, so only a
        change of stance moves it: ``set()``'s ``update_or_create`` and a status
        transition name it. Bookkeeping (``applied``, supersession, a merge's
        re-key) leaves it, or a moved older stance would outrank a newer one.
        """

        if not is_sudo():
            raise PermissionDenied("Write statements through their owning manager verbs.")
        self.changeset = current_changeset(required=True, record=True)
        if kwargs.get("update_fields") is not None:
            kwargs["update_fields"] = (*kwargs["update_fields"], "changeset")
        super().save(*args, **kwargs)

    def delete(self, *args: Any, **kwargs: Any) -> Any:
        raise ValidationError("Statements are retained; withdraw them instead.")

    def lock_subject(self, members: Mapping[str, Any] | None = None) -> Any:
        """Lock this retained statement row for history replay."""

        del members
        manager = type(self)._base_manager.db_manager(hints={"instance": self})
        return manager.all().select_for_update().get(pk=self.pk)

    @classmethod
    def merge_contract(cls, reference: Any, rows: Any, *, survivor: Any) -> None:
        cls.objects.db_manager(rows.db).merge(reference, rows, survivor=survivor)


class StatementPairQuerySet(TrashQuerySet, AngeeHistoryUnscopedQuerySet):
    """Retained pair carriers use the same removal scopes as canonical subjects."""


_StatementPairManager = StatementPairManager.from_queryset(StatementPairQuerySet)


class StatementPair(
    MergeContractMixin, TrashMixin, StatementsMixin, HistoryMixin, SqidMixin, TimestampMixin, RebacMixin
):
    """Ordered pair subject; admission and application stay on its endpoints."""

    history = StatementPairHistory(inherit=True)

    objects = _StatementPairManager()
    _angee_base = _StatementPairManager()
    left: Any
    right: Any
    pair_name: str
    statements: StatementManager[Statement]

    class Meta:
        abstract = True
        base_manager_name = "_angee_base"
        rebac_resource_type = None

    def require_statement_write(self, actor: SubjectRef | None) -> None:
        for endpoint in (self.left, self.right):
            endpoint.require_statement_write(actor)

    def lock_subject(self, members: Mapping[str, Any] | None = None) -> Any:
        """Lock both endpoint owners before their retained pair."""

        for endpoint in (self.left, self.right):
            endpoint.lock_subject()
        return super().lock_subject(members)

    @classmethod
    def merge_contract(cls, reference: Any, rows: Any, *, survivor: Any) -> None:
        """Union third-party stances while retaining the merged pair as evidence."""

        cls.objects.db_manager(rows.db).merge(reference, rows, survivor=survivor)

    def apply_resolved(
        self,
        *,
        aspect: str,
        member: Mapping[str, Any],
        values: Mapping[str, Any],
        statement: Any | None,
        expected_revision: int | None = None,
    ) -> None:
        self.left.apply_pair(aspect=self.pair_name, other=self.right, statement=statement)


class StatementRecords(HistoricalRecords):
    """Generate typed assertions with simple-history's native model generator.

    A concrete MTI child declaring its own aspects gets its own statement table.
    An inherited parent declaration uses the parent's table through the shared pk.
    ``excluded_fields`` excludes copied subject fields, as in simple-history.
    ``link`` names an optional foreign-key model label; the default omits it.
    Native ``bases``, ``history_manager`` and ``historical_queryset`` compose
    domain row behavior and member locking without another generator.
    """

    def __init__(
        self,
        *,
        aspects: Mapping[str, Aspect],
        sqid_prefix: str = "",
        excluded_fields: Sequence[str] = (),
        link: str | None = None,
        bases: Sequence[type[models.Model]] = (Statement,),
        history_manager: type[StatementManager] = StatementManager,
        historical_queryset: type[StatementQuerySet] = StatementQuerySet,
        model_name: str | None = None,
    ) -> None:
        """``model_name`` names the generated model when ``<Subject>Statement`` is taken by a domain model."""

        super().__init__(
            inherit=True,
            bases=bases,
            history_manager=history_manager,
            historical_queryset=historical_queryset,
            excluded_fields=excluded_fields,
            custom_model_name=(lambda name: f"{name}Statement") if model_name is None else (lambda name: model_name),
        )
        self.model_name = model_name
        self.aspects = dict(aspects)
        self.sqid_prefix = sqid_prefix
        self.link = link

    def contribute_to_class(self, cls: Any, name: str) -> None:
        super().contribute_to_class(cls, name)
        # Keep source declarations available for additive abstract donors.
        # Native finalization replaces this with HistoryDescriptor on concrete rows.
        setattr(cls, name, self)

    def aspects_for(self, model: Any) -> dict[str, Any]:
        aspects: dict[str, Any] = {}
        for donor in reversed(model.__mro__):
            if donor is not model and (
                donor is models.Model
                or not issubclass(donor, models.Model)
                or not donor._meta.abstract
                or any(issubclass(parent, donor) for parent in model._meta.parents)
            ):
                continue
            declaration = self if donor is self.cls else donor.__dict__.get(self.manager_name)
            if isinstance(declaration, StatementRecords):
                for key, aspect in declaration.aspects.items():
                    # A conflicting declaration is reported by Statement.check().
                    aspects[key] = None if key in aspects and aspects[key] != aspect else aspect
        return aspects

    def declaration_for(self, model: Any) -> StatementRecords | None:
        """The declaration owning ``model``'s one statement table, or None when a parent's table serves it.

        Declarations are read down the MRO until an inherited concrete table's
        descriptor. The composer orders donors before the subject source in the
        composed bases (``angee.compose.rendering``), so the last declaration at
        this level is the source's own: it owns bases, prefix, queryset and link,
        while every donor contributes its aspects through :meth:`aspects_for`.
        """

        declarations = []
        for owner in model.__mro__:
            declared = owner.__dict__.get(self.manager_name)
            if isinstance(declared, StatementRecords):
                declarations.append(declared)
            elif declared is not None and owner is not model:
                break
        return declarations[-1] if declarations else None

    def finalize(self, sender: Any, **kwargs: Any) -> None:
        if sender._meta.abstract or sender._meta.proxy or not issubclass(sender, self.cls):
            return
        if self.declaration_for(sender) is not self:
            return
        module = importlib.import_module(sender.__module__)
        name = self.get_history_model_name(sender)
        if name.lower() in sender._meta.apps.all_models[sender._meta.app_label] or hasattr(module, name):
            raise ImproperlyConfigured(f"Generated model {name} already exists.")
        model = self.create_history_model(sender, inherited=sender is not self.cls)
        setattr(module, name, model)
        setattr(sender, self.manager_name, HistoryDescriptor(model, self.history_manager, self.historical_queryset))

    def fields_included(self, model: Any) -> list[models.Field]:
        fields = {}
        for aspect in self.aspects_for(model).values():
            if not isinstance(aspect, Aspect):
                continue
            fields.update(
                {
                    name: model._meta.get_field(name)
                    for name in (*aspect.fields, *aspect.members)
                    if not isinstance(aspect.member, Mapping) or name not in aspect.member
                }
            )
            if isinstance(aspect.member, Mapping):
                for name, field in aspect.member.items():
                    if isinstance(field, models.Field):
                        field = copy(field)
                        field.set_attributes_from_name(name)
                        field.model = model
                        fields[name] = field
        return [
            field
            for name, field in sorted(fields.items())
            if name not in self.excluded_fields and Statement.valid_column(name, field)
        ]

    def copy_fields(self, model: Any) -> dict[str, models.Field]:
        fields = super().copy_fields(model)
        for name, field in list(fields.items()):
            currency_field = getattr(field, "angee_currency_field", None)
            if currency_field is not None and currency_field not in fields:
                # A money column copied without its currency column states the amount
                # alone; the subject keeps the currency binding (money's E011 check).
                field = fields[name] = models.DecimalField(
                    max_digits=field.max_digits, decimal_places=field.decimal_places, db_index=field.db_index
                )
            field.null = field.blank = True
            field.default = field.db_default = models.NOT_PROVIDED
            field.auto_created = False
            if isinstance(field, models.ForeignKey):
                field.remote_field.on_delete = models.PROTECT
                field.remote_field.parent_link = False
                field.db_constraint = True
        return fields

    def get_extra_fields(self, model: Any, fields: Any) -> dict[str, Any]:
        return {
            "__module__": model.__module__,
            "sqid_prefix": self.sqid_prefix,
            "statement_subject_model": model,
            "statement_aspects": self.aspects_for(model),
            "statement_link": self.link,
            "subject": models.ForeignKey(model, on_delete=models.PROTECT),
            "aspect": models.CharField(max_length=64, choices={name: name for name in sorted(self.aspects_for(model))}),
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
        constraints = [*Statement._meta.constraints, Statement.stance_constraint()]
        for key, aspect in sorted(self.aspects_for(model).items()):
            if not isinstance(aspect, Aspect):
                continue
            current = Q(aspect=key, superseded__isnull=True)
            for suffix, identity, condition in (
                ("key", ("asserted_by", "subject", "aspect", *aspect.members), current),
                ("applied", ("subject", "aspect", *aspect.members), current & Q(applied=True)),
            ):
                constraints.append(
                    models.UniqueConstraint(
                        fields=identity, condition=condition, name=f"%(app_label)s_%(class)s_{suffix}_{key}"
                    )
                )
            if aspect.members:
                constraints.append(
                    models.CheckConstraint(
                        condition=~Q(aspect=key) | Q(**{f"{member}__isnull": False for member in aspect.members}),
                        name=f"%(app_label)s_%(class)s_member_{key}",
                    )
                )
        constraints.append(
            models.CheckConstraint(
                condition=Q(aspect__in=tuple(sorted(self.aspects_for(model)))), name="%(app_label)s_%(class)s_aspect"
            )
        )
        return {
            "app_label": model._meta.app_label,
            # A named model takes Django's default table for that name, which no other
            # model of the app can hold; the default name keeps "<subject table>_statement".
            "db_table": (
                f"{model._meta.app_label}_{self.model_name.lower()}"
                if self.model_name
                else f"{model._meta.db_table}_statement"
            ),
            "base_manager_name": Statement._meta.base_manager_name,
            "rebac_resource_type": None,
            "constraints": constraints,
            "indexes": Statement._meta.indexes,
        }


class PairAspect(StatementRecords):
    """Declare a canonically ordered carrier with its own generated assertions."""

    def __init__(self, *, link: str | None = None) -> None:
        super().__init__(aspects={}, link=link)
        self.bases = (StatementPair,)
        self.history_manager = StatementPairManager
        self.historical_queryset = StatementPairQuerySet
        self.custom_model_name = lambda name: (
            f"{name}{''.join(part.title() for part in self.manager_name.split('_'))}Pair"
        )

    def fields_included(self, model: Any) -> list[models.Field]:
        return []

    def get_extra_fields(self, model: Any, fields: Any) -> dict[str, Any]:
        return {
            "__module__": model.__module__,
            "pair_name": self.manager_name,
            "left": models.ForeignKey(model, on_delete=models.PROTECT, related_name="+"),
            "right": models.ForeignKey(model, on_delete=models.PROTECT, related_name="+"),
            "statements": StatementRecords(aspects={self.manager_name: Aspect()}, link=self.link),
        }

    def get_meta_options(self, model: Any) -> dict[str, Any]:
        return {
            "app_label": model._meta.app_label,
            "base_manager_name": StatementPair._meta.base_manager_name,
            "rebac_resource_type": None,
            "constraints": [
                models.UniqueConstraint(fields=("left", "right"), name="%(app_label)s_%(class)s_pair"),
                models.CheckConstraint(condition=Q(left__lt=F("right")), name="%(app_label)s_%(class)s_order"),
            ],
        }
