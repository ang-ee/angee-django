"""Subject-bound statement managers and survivorship execution."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from itertools import batched, groupby
from typing import TYPE_CHECKING, Any

from django.core.exceptions import ValidationError
from django.db import models, transaction
from rebac import SubjectRef, current_actor, system_context
from rebac.actors import is_sudo, to_subject_ref
from rebac.errors import PermissionDenied

from angee.base.actors import instance_actor
from angee.base.identity import EvidenceReference, readable_records
from angee.base.querysets import AngeeUnscopedQuerySet, StatementQuerySet
from angee.base.scoping import by_key, query_batch_size, read_scoped_queryset, system_queryset
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
            subject = system_queryset(self.model.statement_subject_model).using(self.db).get(**natural_key)
            subject = self.lock_subject(subject, member_key)
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

    def lock_subject(self, subject: Any, members: Mapping[str, Any]) -> Any:
        """Acquire the subject owner's locks before touching its statements."""

        return subject.lock_statement_subject(members, using=self.db)

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
        if kind is None:
            raise ValidationError("The source must declare a known per-row source_kind.")
        rank = self.model.StatementRank.NORMAL if rank is None else rank
        snak = self.model.StatementSnak.VALUE if snak is None else snak
        if rank == self.model.StatementRank.PREFERRED and kind != self.model.SourceKind.PERSON:
            raise PermissionDenied("Only person sources may assert preferred statements.")
        subject.require_statement_write(source)
        if set(values) - set(declaration.fields) or (snak != self.model.StatementSnak.VALUE and values):
            raise ValidationError("Only value snaks carry the declared aspect's values.")
        refs = validate_value(list[EvidenceReference], list(evidence), field="evidence")
        if kind == self.model.SourceKind.PERSON:
            readable_records(refs, (source,))
        members = declaration.coerce_members(self.model, source, member_key, kind=kind)
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
            if link is not None:
                link.require_access("read", actor=source)
            defaults["link"] = link
        elif link is not None:
            raise ValidationError("This statement declaration has no link model.")
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
            selected = set()
            for batch in by_key(
                system_queryset(self.model.statement_subject_model).using(self.db),
                [subject.pk if isinstance(subject, models.Model) else subject for subject in subjects],
            ):
                selected.update(rows.filter(subject__in=batch.values("pk")).values_list("pk", flat=True))
            ids.intersection_update(selected)
        if supported is not None:
            for key in supported:
                if set(key) != {"subject", "aspect", *self.model.statement_aspects[key["aspect"]].members}:
                    raise ValidationError("Supported keys require subject, aspect and every member.")
            width = max((len(key) for key in supported), default=1)
            for batch in batched(supported, query_batch_size(rows, width=width)):
                supported_filter = models.Q(pk__in=[])
                for key in batch:
                    supported_filter |= models.Q(**key)
                ids.difference_update(rows.filter(supported_filter).values_list("pk", flat=True))
        affected: dict[Any, list[Any]] = {}
        for batch in by_key(rows, sorted(ids)):
            for pk, subject_id in batch.values_list("pk", "subject_id"):
                affected.setdefault(subject_id, []).append(pk)
        count = 0
        for subject_id, subject_keys in sorted(affected.items()):
            with transaction.atomic(using=self.db):
                subject = system_queryset(self.model.statement_subject_model).using(self.db).get(pk=subject_id)
                subject = self.lock_subject(subject, {})
                subject.require_statement_write(source)
                with system_context(reason="statements.transition"):
                    for batch in by_key(rows.filter(subject=subject), subject_keys):
                        for row in batch.order_by("pk").select_for_update():
                            row.status = status
                            row.save(using=self.db, update_fields=("status",))
                            count += 1
                    self.resolve(subject=subject)
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

    def quarantine(self, asserted_by: Any, subjects: Sequence[Any] | None = None) -> int:
        return self._transition(self.model.StatementStatus.QUARANTINED, asserted_by=asserted_by, subjects=subjects)

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
                self._transition(
                    self.model.StatementStatus.WITHDRAWN, asserted_by=SubjectRef.parse(source), changeset=changeset
                )
                for source in sources
            ]
            return sum(counts)

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
            subject = self.db_manager(using).lock_subject(subject, {})
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
                asserted = [row for row in candidates if row.status == self.model.StatementStatus.ASSERTED]
                preferred = sorted(
                    (row for row in asserted if row.rank == self.model.StatementRank.PREFERRED),
                    key=lambda row: row.sort_key(),
                )
                normal = subject.statement_candidates(
                    key[0],
                    [row for row in asserted if row.rank == self.model.StatementRank.NORMAL],
                )
                if any(row not in asserted or row.rank != self.model.StatementRank.NORMAL for row in normal):
                    raise ValidationError("Candidate policy may only select asserted normal statements.")
                ordered = sorted(normal, key=lambda row: declaration.survivorship.sort_key(declaration, row))
                winner = (
                    preferred[0]
                    if preferred
                    else next((row for row in ordered if row.snak == self.model.StatementSnak.VALUE), None)
                )
                values = {
                    name: winner._meta.get_field(name).value_from_object(winner)
                    if winner is not None and winner.snak == self.model.StatementSnak.VALUE
                    else None
                    for name in declaration.fields
                }
                if winner is not None and winner.snak == self.model.StatementSnak.VALUE and declaration.allow_fill:
                    for name, value in values.items():
                        if value in (None, ""):
                            values[name] = next(
                                (
                                    row._meta.get_field(name).value_from_object(row)
                                    for row in ordered
                                    if row.snak == self.model.StatementSnak.VALUE
                                    and row._meta.get_field(name).value_from_object(row) not in (None, "")
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
