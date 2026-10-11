"""Subject-bound statement managers and survivorship execution."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from datetime import datetime
from itertools import groupby
from typing import Any

from django.core.exceptions import ValidationError
from django.db import models, transaction
from rebac import SubjectRef, current_actor, system_context
from rebac.actors import is_sudo, to_subject_ref
from rebac.errors import PermissionDenied
from rebac.managers import TrackedQuerySet
from rebac.resources import model_for_subject_type
from simple_history.manager import HistoryManager

from angee.base.actors import instance_actor
from angee.base.mixins import (
    Aspect,
    OptimisticLockMixin,
    RecentSurvivorship,
    Snak,
    SourceKind,
    StatementRank,
    StatementStatus,
    Survivorship,
    update_fields_with_auto_now,
)
from angee.base.querysets import StatementQuerySet
from angee.base.scoping import read_scoped_queryset, system_queryset
from angee.base.validation import validate_value


class StatementManager(HistoryManager.from_queryset(StatementQuerySet)):  # type: ignore[misc]
    """Locked upserts and survivorship on the generated assertion model."""

    def get_queryset(self) -> Any:
        rows = self.get_super_queryset()
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
        rank: str = StatementRank.NORMAL,
        snak: str = Snak.VALUE,
        basis_hash: str = "",
        **natural_key: Any,
    ) -> Any:
        """Authorize the source, then update_or_create under the subject lock.

        Per-aspect partial keys cannot arbitrate Django's bulk conflict upserts.
        Equal assertions return without a save or a historical row.
        """
        # Evidence models depend on this foundation; decode through their owner.
        from angee.base.evidence import EvidenceReference, readable_records

        declaration = self.model.statement_aspects[aspect]
        requester = instance_actor(self.instance) if self.instance is not None else current_actor()
        source = to_subject_ref(asserted_by if asserted_by is not None else requester)
        if source != requester and not is_sudo():
            raise PermissionDenied("Only the source may assert its statements.")
        resolved = model_for_subject_type(source.subject_type)
        if resolved is None or source.optional_relation or source.subject_id == "*":
            raise ValidationError("A statement source must be one persisted principal.")
        model, id_attr = resolved
        principal = system_queryset(model).get(**{id_attr: source.subject_id})
        try:
            kind = SourceKind(getattr(principal, "source_kind", None))
        except ValueError as error:
            raise ValidationError("The source must declare a known per-row source_kind.") from error
        if rank == StatementRank.PREFERRED and kind != SourceKind.PERSON:
            raise PermissionDenied("Only person sources may assert preferred statements.")
        members = {name: natural_key.pop(name) for name in declaration.members if name in natural_key}
        if set(members) != set(declaration.members) or any(value is None for value in members.values()):
            raise ValidationError("Every aspect member is required.")
        if set(values) - set(declaration.fields) or (snak != Snak.VALUE and values):
            raise ValidationError("Only value snaks carry the declared aspect's values.")
        refs = validate_value(list[EvidenceReference], list(evidence), field="evidence")
        bound = self.instance
        if bound is not None:
            if natural_key:
                raise ValidationError("A bound statement manager already has its subject.")
            natural_key = {"pk": bound.pk}
        if not natural_key:
            raise ValidationError("Statements require a subject natural key.")
        with transaction.atomic():
            subject = system_queryset(self.model.statement_subject_model, lock=("self",)).get(**natural_key)
            subject.require_statement_write(source)
            readable_records(refs, (source,))
            for name, value in members.items():
                field = self.model._meta.get_field(name)
                if isinstance(field, models.ForeignKey):
                    pk = getattr(value, field.target_field.attname) if isinstance(value, models.Model) else value
                    members[name] = read_scoped_queryset(field.remote_field.model, source).get(
                        **{field.target_field.name: pk},
                    )
            if link is not None:
                link.require_access("read", actor=source)
            defaults = {name: values.get(name) for name in declaration.fields}
            defaults.update(
                snak=snak,
                confidence=confidence,
                rank=rank,
                status=StatementStatus.ASSERTED,
                source_kind=kind,
                evidence=[ref.model_dump(mode="json") for ref in refs],
                basis_hash=basis_hash,
            )
            if "link" in {field.name for field in self.model._meta.fields}:
                defaults["link"] = link
            elif link is not None:
                raise ValidationError("This statement declaration excludes link.")
            key = dict(subject=subject, asserted_by=str(source), aspect=aspect, superseded=None, **members)
            candidate = self.model(**key, **defaults)
            candidate.full_clean(validate_unique=False, validate_constraints=False)
            existing = self.model._base_manager.filter(**key).first()
            if existing is not None and all(
                getattr(existing, self.model._meta.get_field(name).attname)
                == getattr(candidate, self.model._meta.get_field(name).attname)
                for name in defaults
            ):
                return existing
            with system_context(reason="statements.set"):
                statement, _ = self.model._base_manager.update_or_create(**key, defaults=defaults)
                self.resolve(subject=subject)
            statement.refresh_from_db()
            return statement

    def _transition(
        self,
        status: str,
        *,
        asserted_by: Any,
        subjects: Sequence[Any] | None = None,
        supported: Sequence[Mapping[str, Any]] | None = None,
        changeset: Any = None,
    ) -> int:
        source = to_subject_ref(asserted_by)
        requester = instance_actor(self.instance) if self.instance is not None else current_actor()
        if source != requester and not is_sudo():
            raise PermissionDenied("Only the source may change its statement stance.")
        rows = self.model._base_manager.filter(asserted_by=str(source), superseded__isnull=True)
        if (bound := self.instance) is not None:
            rows = rows.filter(subject=bound)
        if subjects is not None:
            rows = rows.filter(subject__in=subjects)
        if changeset is not None:
            rows = rows.filter(changeset=changeset)
        if supported is not None:
            keys = models.Q(pk__in=[])
            for key in supported:
                if set(key) != {"subject", "aspect", *self.model.statement_aspects[key["aspect"]].members}:
                    raise ValidationError("Supported keys require subject, aspect and every member.")
                keys |= models.Q(**key)
            rows = rows.exclude(keys)
        count = 0
        with transaction.atomic():
            for subject_id in rows.order_by("subject_id").values_list("subject_id", flat=True).distinct():
                subject = system_queryset(self.model.statement_subject_model, lock=("self",)).get(pk=subject_id)
                subject.require_statement_write(source)
                with system_context(reason="statements.transition"):
                    for row in rows.filter(subject=subject).exclude(status=status).order_by("pk"):
                        row.status = status
                        row.save(update_fields=update_fields_with_auto_now(row, ("status",)))
                        count += 1
                    self.resolve(subject=subject)
        return count

    def withdraw(self, asserted_by: Any, subjects: Sequence[Any] | None = None) -> int:
        return self._transition(StatementStatus.WITHDRAWN, asserted_by=asserted_by, subjects=subjects)

    def withdraw_unsupported(self, asserted_by: Any, supported: Sequence[Mapping[str, Any]]) -> int:
        return self._transition(StatementStatus.WITHDRAWN, asserted_by=asserted_by, supported=supported)

    def quarantine(self, asserted_by: Any, subjects: Sequence[Any] | None = None) -> int:
        return self._transition(StatementStatus.QUARANTINED, asserted_by=asserted_by, subjects=subjects)

    def withdraw_changeset(self, changeset: Any) -> int:
        with transaction.atomic():
            sources = (
                self.model._base_manager.filter(changeset=changeset)
                .order_by("asserted_by")
                .values_list(
                    "asserted_by",
                    flat=True,
                )
                .distinct()
            )
            counts = [
                self._transition(StatementStatus.WITHDRAWN, asserted_by=SubjectRef.parse(source), changeset=changeset)
                for source in sources
            ]
            return sum(counts)

    def _order(self, aspect: Aspect, row: Any) -> tuple[Any, ...]:
        priority = (
            aspect.precedence.index(row.source_kind) if row.source_kind in aspect.precedence else len(aspect.precedence)
        )
        tie = (priority, -row.confidence, row.asserted_by, row.pk)
        if aspect.survivorship == Survivorship.HIGHEST_TRUST:
            return (-row.confidence, priority, row.asserted_by, row.pk)
        if aspect.survivorship == Survivorship.MOST_COMPLETE:
            complete = sum(row._meta.get_field(name).value_from_object(row) not in (None, "") for name in aspect.fields)
            return (-complete, *tie)
        if isinstance(aspect.survivorship, RecentSurvivorship):
            value = getattr(row, aspect.survivorship.date_field)
            recency = value.timestamp() if isinstance(value, datetime) else value.toordinal() if value else 0
            return (value is None, -recency, *tie)
        return tie

    def resolve(self, *, subject: Any = None) -> None:
        """Resolve each field group atomically; normal unknowns never erase values."""
        subject = subject if subject is not None else self.instance
        if subject is None:
            raise ValidationError("Survivorship requires one subject.")
        actor = instance_actor(subject)
        if not is_sudo():
            subject.require_statement_write(actor)
        with transaction.atomic(), system_context(reason="statements.resolve"):
            subject = system_queryset(self.model.statement_subject_model, lock=("self",)).get(pk=subject.pk)
            rows = self.model._base_manager.filter(subject=subject, superseded__isnull=True)
            members = sorted({name for aspect in self.model.statement_aspects.values() for name in aspect.members})

            def group(row: Any) -> tuple[Any, ...]:
                return (
                    row.aspect,
                    *(
                        row._meta.get_field(name).value_from_object(row)
                        for name in self.model.statement_aspects[row.aspect].members
                    ),
                )

            for key, grouped in groupby(rows.order_by("aspect", *members), key=group):
                candidates = list(grouped)
                declaration = self.model.statement_aspects[key[0]]
                asserted = [row for row in candidates if row.status == StatementStatus.ASSERTED]
                preferred = sorted(
                    (row for row in asserted if row.rank == StatementRank.PREFERRED),
                    key=lambda row: (row.snak != Snak.NO_VALUE, row.asserted_by, row.pk),
                )
                normal = subject.statement_candidates(
                    key[0],
                    [row for row in asserted if row.rank == StatementRank.NORMAL],
                )
                if any(row not in asserted or row.rank != StatementRank.NORMAL for row in normal):
                    raise ValidationError("Candidate policy may only select asserted normal statements.")
                ordered = sorted(normal, key=lambda row: self._order(declaration, row))
                winner = preferred[0] if preferred else next((row for row in ordered if row.snak == Snak.VALUE), None)
                values = {
                    name: winner._meta.get_field(name).value_from_object(winner)
                    if winner is not None and winner.snak == Snak.VALUE
                    else None
                    for name in declaration.fields
                }
                if winner is not None and winner.snak == Snak.VALUE and declaration.allow_fill:
                    for name, value in values.items():
                        if value in (None, ""):
                            values[name] = next(
                                (
                                    row._meta.get_field(name).value_from_object(row)
                                    for row in ordered
                                    if row.snak == Snak.VALUE
                                    and row._meta.get_field(name).value_from_object(row) not in (None, "")
                                ),
                                value,
                            )
                for row in sorted(candidates, key=lambda row: row is winner):
                    if row.applied != (row is winner):
                        row.applied = row is winner
                        row.save(update_fields=update_fields_with_auto_now(row, ("applied",)))
                if (winner is not None and winner.snak == Snak.SOME_VALUE) or (
                    winner is None and any(row.snak == Snak.SOME_VALUE for row in asserted)
                ):
                    continue
                expected = (
                    getattr(subject, subject.REVISION_FIELD) if isinstance(subject, OptimisticLockMixin) else None
                )
                subject.apply_resolved(
                    aspect=key[0],
                    member=dict(zip(declaration.members, key[1:], strict=True)),
                    values=values,
                    statement=winner,
                    expected_revision=expected,
                )


class StatementPairManager(HistoryManager.from_queryset(TrackedQuerySet)):  # type: ignore[misc]
    """Bind ordered carriers to an endpoint; endpoints own pair admission."""

    def get_queryset(self) -> Any:
        rows = self.get_super_queryset()
        return (
            rows if self.instance is None else rows.filter(models.Q(left=self.instance) | models.Q(right=self.instance))
        )

    def for_pair(self, left: Any, right: Any) -> Any:
        model = self.model._meta.get_field("left").remote_field.model
        if not all(isinstance(row, model) and row.pk is not None for row in (left, right)) or left.pk == right.pk:
            raise ValidationError("A pair requires two distinct persisted endpoints of its declared model.")
        actor = instance_actor(left)
        for endpoint in (left, right):
            if not read_scoped_queryset(model, actor).filter(pk=endpoint.pk).exists():
                raise PermissionDenied("A pair requires read on both endpoints.")
        ordered = sorted((left.pk, right.pk))
        with transaction.atomic():
            list(system_queryset(model, lock=("self",)).filter(pk__in=ordered).order_by("pk"))
            pair, _ = self.model._base_manager.get_or_create(left_id=ordered[0], right_id=ordered[1])
        return pair
