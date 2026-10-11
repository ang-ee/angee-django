"""Subject-bound statement managers and survivorship execution."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from itertools import groupby
from typing import TYPE_CHECKING, Any

from django.core.exceptions import ValidationError
from django.db import models, transaction
from rebac import SubjectRef, current_actor, system_context
from rebac.actors import is_sudo, to_subject_ref
from rebac.errors import PermissionDenied

from angee.base.actors import instance_actor
from angee.base.identity import EvidenceReference, readable_records
from angee.base.querysets import AngeeUnscopedQuerySet, StatementQuerySet, model_history
from angee.base.scoping import read_scoped_queryset, system_queryset
from angee.base.validation import validate_value

if TYPE_CHECKING:
    from angee.base.mixins import Aspect


class StatementManager(models.Manager.from_queryset(StatementQuerySet)):  # type: ignore[misc]
    """Locked upserts and survivorship on the generated assertion model."""

    def __init__(self, model: Any = None, instance: Any = None) -> None:
        super().__init__()
        self.model = model
        self.instance = instance
        if instance is not None:
            self._db = instance._state.db

    def get_queryset(self) -> Any:
        rows = super().get_queryset()
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
            subject = (
                system_queryset(self.model.statement_subject_model, lock=("self",)).using(self.db).get(**natural_key)
            )
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
                self.resolve(subject=subject)
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
        kind = self.model.source_kind_for(source)
        rank = self.model.Rank.NORMAL if rank is None else rank
        snak = self.model.Snak.VALUE if snak is None else snak
        if rank == self.model.Rank.PREFERRED and kind != self.model.SourceKind.PERSON:
            raise PermissionDenied("Only person sources may assert preferred statements.")
        subject.require_statement_write(source)
        if set(values) - set(declaration.fields) or (snak != self.model.Snak.VALUE and values):
            raise ValidationError("Only value snaks carry the declared aspect's values.")
        refs = validate_value(list[EvidenceReference], list(evidence), field="evidence")
        readable_records(refs, (source,))
        members = declaration.coerce_members(self.model, source, member_key)
        defaults = {name: values.get(name) for name in declaration.fields}
        defaults.update(
            snak=snak,
            confidence=confidence,
            rank=rank,
            status=self.model.Status.ASSERTED,
            source_kind=kind,
            evidence=[ref.model_dump(mode="json") for ref in refs],
            basis_hash=basis_hash,
        )
        if self.model.statement_link:
            if link is not None:
                link.require_access("read", actor=source)
            defaults["link"] = link
        elif link is not None:
            raise ValidationError("This statement declaration has link=False.")
        return source, members, defaults

    def _transition(
        self,
        status: str,
        *,
        asserted_by: Any,
        subjects: Sequence[Any] | None = None,
        supported: Sequence[Mapping[str, Any]] | None = None,
        changeset: Any = None,
        aspect: str | None = None,
        link: Any = None,
    ) -> int:
        source = self.asserting_source(asserted_by)
        rows = self.model._base_manager.filter(asserted_by=str(source), superseded__isnull=True)
        if (bound := self.instance) is not None:
            rows = rows.filter(subject=bound)
        if aspect is not None:
            rows = rows.filter(aspect=aspect)
        if link is not None:
            rows = rows.filter(link=link)
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
                        row.save(update_fields=("status",))
                        count += 1
                    self.resolve(subject=subject)
        return count

    def withdraw(self, asserted_by: Any, subjects: Sequence[Any] | None = None, *, link: Any = None) -> int:
        return self._transition(self.model.Status.WITHDRAWN, asserted_by=asserted_by, subjects=subjects, link=link)

    def withdraw_unsupported(
        self, asserted_by: Any, supported: Sequence[Mapping[str, Any]], *, aspect: str | None = None, link: Any = None
    ) -> int:
        return self._transition(
            self.model.Status.WITHDRAWN, asserted_by=asserted_by, supported=supported, aspect=aspect, link=link
        )

    def quarantine(self, asserted_by: Any, subjects: Sequence[Any] | None = None) -> int:
        return self._transition(self.model.Status.QUARANTINED, asserted_by=asserted_by, subjects=subjects)

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
                self._transition(self.model.Status.WITHDRAWN, asserted_by=SubjectRef.parse(source), changeset=changeset)
                for source in sources
            ]
            return sum(counts)

    def revert(self, changeset: Any, *, resolve: bool = True) -> list[Any]:
        """Restore pre-boundary assertions; first assertions withdraw, never delete.

        Stage current keys outside uniqueness before undoing a merge collapse.
        The restored stance and supersession are historical facts; attribution of
        this write belongs to the new changeset. Admission belongs to the subjects,
        rather than to the original asserting principal.
        """

        using = self.db
        history = model_history(self.model)
        if history is None:
            raise ValidationError("Statement revert requires registered history.")
        with transaction.atomic(using=using):
            states = list(history.changeset_states(self.model, changeset, using=using))
            subject_ids = {version.subject_id for state in states for version in state if version is not None}
            subjects = list(
                system_queryset(self.model.statement_subject_model, lock=("self",))
                .using(using)
                .filter(pk__in=subject_ids)
                .order_by("pk")
            )
            missing = subject_ids - {subject.pk for subject in subjects}
            if missing:
                raise ValidationError(
                    f"{self.model.statement_subject_model._meta.label} {sorted(missing)}: "
                    "retained subjects are missing."
                )
            if not is_sudo():
                for subject in subjects:
                    subject.require_statement_write(
                        instance_actor(self.instance) if self.instance is not None else current_actor()
                    )
            with system_context(reason="statements.revert"):
                for first, _, _ in states:
                    row = self.model._base_manager.using(using).filter(pk=first.id).first()
                    if row is None:
                        raise ValidationError(f"{self.model._meta.label} {first.id}: a retained statement is missing.")
                    row.superseded = row
                    row.applied = False
                    row.save(using=using, update_fields=("superseded", "applied"))
                # Restore occupied keys first; new withdrawn assertions remain
                # outside those keys when the pre-state already occupies one.
                for first, _, previous in sorted(states, key=lambda state: state[2] is None):
                    row = (previous or first).instance
                    row.applied = False
                    if previous is None:
                        row.status = self.model.Status.WITHDRAWN
                        row.superseded = (
                            self.model._base_manager.using(using)
                            .filter(
                                **row.current_key(),
                                superseded__isnull=True,
                            )
                            .exclude(pk=row.pk)
                            .first()
                        )
                    row.save(using=using)
                if resolve:
                    for subject in subjects:
                        self.resolve(subject=subject)
            return subjects

    def resolve(self, *, subject: Any = None) -> None:
        """Resolve each field group atomically; normal unknowns never erase values."""
        subject = subject if subject is not None else self.instance
        if subject is None:
            raise ValidationError("Survivorship requires one subject.")
        actor = instance_actor(subject)
        if not is_sudo():
            subject.require_statement_write(actor)
        using = subject._state.db or self.db
        with transaction.atomic(using=using), system_context(reason="statements.resolve"):
            subject = (
                system_queryset(self.model.statement_subject_model, lock=("self",)).using(using).get(pk=subject.pk)
            )
            rows = self.model._base_manager.using(using).filter(subject=subject, superseded__isnull=True)
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
                asserted = [row for row in candidates if row.status == self.model.Status.ASSERTED]
                preferred = sorted(
                    (row for row in asserted if row.rank == self.model.Rank.PREFERRED),
                    key=lambda row: row.sort_key(),
                )
                normal = subject.statement_candidates(
                    key[0],
                    [row for row in asserted if row.rank == self.model.Rank.NORMAL],
                )
                if any(row not in asserted or row.rank != self.model.Rank.NORMAL for row in normal):
                    raise ValidationError("Candidate policy may only select asserted normal statements.")
                ordered = sorted(normal, key=lambda row: declaration.survivorship.sort_key(declaration, row))
                winner = (
                    preferred[0]
                    if preferred
                    else next((row for row in ordered if row.snak == self.model.Snak.VALUE), None)
                )
                values = {
                    name: winner._meta.get_field(name).value_from_object(winner)
                    if winner is not None and winner.snak == self.model.Snak.VALUE
                    else None
                    for name in declaration.fields
                }
                if winner is not None and winner.snak == self.model.Snak.VALUE and declaration.allow_fill:
                    for name, value in values.items():
                        if value in (None, ""):
                            values[name] = next(
                                (
                                    row._meta.get_field(name).value_from_object(row)
                                    for row in ordered
                                    if row.snak == self.model.Snak.VALUE
                                    and row._meta.get_field(name).value_from_object(row) not in (None, "")
                                ),
                                value,
                            )
                for row in sorted(candidates, key=lambda row: row is winner):
                    if row.applied != (row is winner):
                        row.applied = row is winner
                        row.save(using=using, update_fields=("applied",))
                if (winner is not None and winner.snak == self.model.Snak.SOME_VALUE) or (
                    winner is None and any(row.snak == self.model.Snak.SOME_VALUE for row in asserted)
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
        """Retain both assertions on key collisions, then resolve the moved subject."""
        target = reference.lookup(survivor) if reference is not None else {"subject_id": survivor.pk}
        columns = reference.columns if reference is not None else ("subject",)
        subjects = set()
        for row in rows.order_by("pk"):
            if row.superseded_id is None:
                key = dict(row.current_key(), superseded__isnull=True)
                key.update(target)
                kept = self.model._base_manager.filter(**key).exclude(pk=row.pk).first()
                if kept is not None:
                    winner = min((kept, row), key=lambda item: item.sort_key())
                    loser = row if winner is kept else kept
                    loser.superseded = winner
                    loser.applied = False
                    loser.save(update_fields=("superseded", "applied"))
            for name, value in target.items():
                setattr(row, name, value)
            # Applied rows from different sources also share the destination's key.
            row.applied = False
            row.save(update_fields=(*columns, "applied"))
            subjects.add(row.subject_id)
        for subject_id in sorted(subjects):
            subject = self.model.statement_subject_model._base_manager.get(pk=subject_id)
            self.resolve(subject=subject)


class StatementPairManager(models.Manager.from_queryset(AngeeUnscopedQuerySet)):  # type: ignore[misc]
    """Bind ordered carriers to an endpoint; endpoints own pair admission."""

    def __init__(self, model: Any = None, instance: Any = None) -> None:
        super().__init__()
        self.model = model
        self.instance = instance

    def get_queryset(self) -> Any:
        rows = super().get_queryset()
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
            target = self.for_pair(survivor, other)
            self.model.statements.merge(None, pair.statements.all(), survivor=target)
            pair.trash(reason="Merged pair endpoints")
