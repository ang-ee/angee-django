"""Admission and final transitions, serialized group first, then decision."""

import logging
from collections.abc import Iterator, Sequence
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Any

from django.apps import apps
from django.contrib.auth import get_user_model
from django.core.exceptions import PermissionDenied, ValidationError
from django.db import IntegrityError, transaction
from django.db.models import BooleanField, ExpressionWrapper, F, Q
from django.db.models.deletion import ProtectedError
from django.db.models.functions import Now
from rebac import current_actor, system_context, to_subject_ref

from angee.base.actors import actor_user_id
from angee.base.identity import public_id_of
from angee.base.impl import ImplBase
from angee.base.mixins import AppendOnlyQuerySet
from angee.base.models import AngeeManager, AngeeQuerySet
from angee.base.refs import canonical_record_model, canonical_record_target
from angee.base.scoping import lock_if_supported, system_queryset
from angee.decisions.contracts import (
    DEFAULT_REQUESTER,
    DecisionContext,
    DecisionRecordReference,
    DecisionRequest,
    readable_records,
)
from angee.decisions.exceptions import ResolverAuthorityError, RetryableDecisionError
from angee.decisions.forms import Action, compile_form, relation_candidates, validate_form
from angee.decisions.signals import decision_group_settled
from angee.decisions.states import OPEN_DECISION, ClosedReason
from angee.graphql.publishing import publish_change

logger = logging.getLogger(__name__)


def _user(actor: Any, *, active: bool = True) -> Any:
    identity = actor_user_id(to_subject_ref(actor if actor is not None else current_actor()))
    user = system_queryset(get_user_model()).filter(pk=identity).first()
    if user is None or active and not user.is_active:
        raise PermissionDenied("An active user is required.")
    return user


class DecisionGroupQuerySet(AppendOnlyQuerySet[Any], AngeeQuerySet):
    """Own immutable group admission and its single final timestamp."""

    @contextmanager
    def hold(self, group_id: Any, *, skip_locked: bool = False) -> Iterator[Any]:
        """Lock the group before seats, allowing incoming references while it is held."""
        with transaction.atomic(), system_context(reason="decisions.group_transition"):
            group = lock_if_supported(self.filter(pk=group_id), no_key=True, skip_locked=skip_locked).first()
            if group is None and not skip_locked:
                raise ValidationError("The decision group no longer exists.")
            yield group

    def settled(self) -> Any:
        """Select groups whose settlement is final."""
        return self.filter(settled_at__isnull=False)

    def settle(self) -> int:
        """Set the settlement timestamp exactly once."""
        return self.filter(settled_at__isnull=True).owner_update(settled_at=Now(), updated_at=Now())


DecisionGroupManager = AngeeManager.from_queryset(DecisionGroupQuerySet)


class DecisionQuerySet(AppendOnlyQuerySet[Any], AngeeQuerySet):
    """Own conditional answer and closure writes on retained seats."""

    @contextmanager
    def hold(self, decision_id: Any, *, skip_locked: bool = False) -> Iterator[tuple[Any, Any]]:
        """Yield a group and seat locked in that order; absent rows have defined errors."""
        group_id = system_queryset(self.model).filter(pk=decision_id).values_list("group_id", flat=True).first()
        if group_id is None:
            raise ValidationError("The decision no longer exists.")
        group_model = self.model._meta.get_field("group").related_model
        with group_model.objects.hold(group_id, skip_locked=skip_locked) as group:
            decision = lock_if_supported(self.filter(pk=decision_id), no_key=True).first() if group else None
            if group is not None and decision is None:
                raise ValidationError("The decision no longer exists.")
            yield group, decision

    def pending(self) -> Any:
        """Select stored unanswered seats, including deadlines awaiting expiry."""
        return self.filter(OPEN_DECISION)

    def with_open_state(self) -> Any:
        """Project live answerability once, including deadlines not yet swept."""
        return self.annotate(_is_open=self.open_expression())

    def open_expression(self) -> ExpressionWrapper:
        """Own the live predicate used by open rows, resource fields and filters."""
        return ExpressionWrapper(
            OPEN_DECISION & (Q(expires_at__isnull=True) | Q(expires_at__gt=Now())), output_field=BooleanField(),
        )

    def open(self) -> Any:
        """Select seats still accepting answers at the database's current time."""
        return self.with_open_state().filter(_is_open=True)

    def unanswered(self) -> Any:
        """Select closures that prevent a waiter from applying answers."""
        return self.filter(closed_reason__in=ClosedReason.unanswered_values())

    def due(self) -> Any:
        """Select open seats whose deadline has passed according to the database."""
        return self.pending().filter(expires_at__lte=Now())

    def resolve(self, *, revision: int, verdict: str, resolution: dict[str, Any], resolver_id: Any) -> int:
        """Record one answer only while its revision and database deadline remain valid."""
        return self.open().filter(revision=revision).owner_update(
            verdict=verdict, resolution=resolution, resolved_by_id=resolver_id, resolved_at=Now(),
            closed_reason=ClosedReason.RESOLVED, revision=F("revision") + 1, updated_at=Now(),
        )

    def close(self, reason: str, *, superseded_by: Any = None) -> int:
        """Close unanswered seats without rewriting any final answer."""
        if reason not in ClosedReason.values or reason == ClosedReason.RESOLVED:
            raise ValidationError("An unanswered decision needs a closure reason.")
        return self.pending().owner_update(
            closed_reason=reason, superseded_by=superseded_by, revision=F("revision") + 1, updated_at=Now(),
        )

    def reject_attempt(self) -> None:
        """Consume one invalid submission and close seats that exhausted their attempts."""
        self.open().owner_update(
            invalid_attempts=F("invalid_attempts") + 1, revision=F("revision") + 1, updated_at=Now(),
        )
        self.open().filter(invalid_attempts__gte=F("max_attempts")).close(ClosedReason.INVALID_ATTEMPTS)


@dataclass(frozen=True)
class ResolvedDecision:
    """A retained seat with its revalidated action and currently authorized resolver."""

    decision: Any
    action: Action | None
    resolver: Any
    basis: Any


@dataclass
class _Admission:
    request: DecisionRequest
    assignees: tuple[Any, ...]
    schema: dict[str, Any]
    subject: tuple[Any, Any]
    targets: set[tuple[Any, Any]] = field(default_factory=set)
    expires_after: timedelta | None = None


class DecisionManager(AngeeManager.from_queryset(DecisionQuerySet)):  # type: ignore[misc]
    """Admit immutable questions and orchestrate group-owned final transitions."""

    def admit_group(self, requests: Sequence[DecisionRequest], *, actor: Any, policy: str = "first") -> Any:
        """Admit all seats atomically, checking standing grants without creating any."""
        issuer = _user(actor)
        group_model = self.model._meta.get_field("group").related_model
        group_model._meta.get_field("policy").resolve_class(policy)
        if not requests:
            raise ValidationError("A decision group needs at least one seat.")
        prepared = [_Admission(request, tuple(_user(person, active=False) for person in request.assignees),
                     compile_form(request.actions, initial=request.initial, refine=request.refine),
                     canonical_record_target(request.subject) if request.subject is not None else (None, None))
                    for request in requests]
        return self._admit_group(prepared, issuer=issuer, policy=policy)

    def reask(
        self, group_id: Any, *, actor: Any, actions: Sequence[type[Action]], errors: dict[str, list[str]],
    ) -> Any:
        """Re-admit every frozen seat, retaining the old answers and new field errors.

        Admission rechecks standing evidence permissions. Frozen forms, basis,
        requester and assignees stay unchanged even when action code changes.
        """
        issuer = _user(actor)
        group_model = self.model._meta.get_field("group").related_model
        with group_model.objects.hold(group_id) as group:
            group.require_access("read", issuer)
            if group.settled_at is None:
                raise ValidationError("Only a settled group can be asked again.")
            prepared = []
            for decision in lock_if_supported(group.decisions.order_by("index"), no_key=True):
                decision.require_access("read", issuer)
                offered = decision.form_schema["properties"]["action"]["enum"]
                request = DecisionRequest(
                    kind=decision.kind, subject=decision.subject,
                    assignees=tuple(decision.assignees.all()), requester=decision.requester,
                    actions=tuple(action for action in actions if action.value in offered), basis=decision.basis,
                    context=DecisionContext.model_validate(decision.context), supersede=decision.supersede,
                    max_attempts=decision.max_attempts, errors=errors,
                )
                prepared.append(_Admission(
                    request, tuple(_user(person, active=False) for person in request.assignees), decision.form_schema,
                    (decision.subject_content_type, decision.subject_object_id),
                    expires_after=(decision.expires_at - decision.created_at if decision.expires_at else None),
                ))
            return self._admit_group(prepared, issuer=issuer, policy=group.policy, reasked_from=group)

    def _admit_group(self, prepared: list[_Admission], *, issuer: Any, policy: str, reasked_from: Any = None) -> Any:
        group_model = self.model._meta.get_field("group").related_model
        identities = {(item.request.kind, *item.subject) for item in prepared if item.request.supersede}
        for identity in identities:
            if sum((item.request.kind, *item.subject) == identity for item in prepared) != 1:
                raise ValidationError("Supersession requires one seat per kind and subject in a group.")
        try:
            with transaction.atomic(), system_context(reason="decisions.admit"):
                group = group_model.objects.create(policy=policy, issuer=issuer, reasked_from=reasked_from)
                old = list(self.pending().filter(Q(*[
                    Q(kind=kind, subject_content_type=ct, subject_object_id=pk) for kind, ct, pk in identities
                ], _connector=Q.OR)).order_by("group_id", "pk")) if identities else []
                retained = self._prepare_evidence(prepared, issuer)
                old_groups = self._lock_admission(old, retained)
                for index, item in enumerate(prepared):
                    previous_ids = [d.pk for d in old if (d.kind, d.subject_content_type_id, d.subject_object_id) == (
                        item.request.kind, item.subject[0].pk if item.subject[0] else None, item.subject[1],
                    ) and d.is_pending]
                    closed = self.filter(pk__in=previous_ids).pending()
                    # Free the partial unique key before inserting its replacement.
                    closed_ids = list(closed.values_list("pk", flat=True))
                    closed.close(ClosedReason.SUPERSEDED)
                    decision = self._admit_seat(group, index, item, issuer)
                    self.filter(pk__in=closed_ids).owner_update(superseded_by=decision)
                    for previous in old:
                        if previous.pk in closed_ids:
                            self._publish(previous)
                for previous_group in old_groups:
                    self._settle(previous_group)
        except IntegrityError as error:
            constraint = getattr(getattr(error.__cause__, "diag", None), "constraint_name", None)
            if constraint == "decisions_open_subject_unique" or (
                "UNIQUE constraint failed:" in str(error) and "subject_content_type_id" in str(error)
                and "subject_object_id" in str(error)
            ):
                raise RetryableDecisionError(
                    "A superseding question was admitted concurrently; retry admission.",
                ) from error
            raise
        return group.with_actor(issuer)

    def _prepare_evidence(self, prepared: list[_Admission], issuer: Any) -> list[tuple[Any, Any]]:
        retained = {}
        for item in prepared:
            request = item.request
            participants = (issuer, *item.assignees)
            refs = list(request.context.records())
            if request.subject is not None:
                refs.append(DecisionRecordReference(
                    model=request.subject._meta.label, id=public_id_of(request.subject),
                ))
            records = readable_records(tuple(refs), participants)
            for candidate in relation_candidates(item.schema):
                candidates = tuple(DecisionRecordReference(model=candidate.model, id=value) for value in candidate.ids)
                readable_records(candidates, participants, permission=candidate.permission)
            context_refs = {(ref.model.lower(), ref.id) for ref in request.context.records()}
            for record in records:
                if (record._meta.label_lower, public_id_of(record)) in context_refs:
                    target = canonical_record_target(record)
                    item.targets.add(target)
                    retained[(target.content_type.pk, target.object_id)] = (
                        canonical_record_model(type(record)), record.pk,
                    )
        return [retained[key] for key in sorted(retained)]

    def _lock_admission(self, old: list[Any], retained: list[tuple[Any, Any]]) -> list[Any]:
        group_model = self.model._meta.get_field("group").related_model
        evidence_model = apps.get_model("decisions", "DecisionEvidence")
        old_ids = {decision.group_id for decision in old}
        group_ids = old_ids | {pk for model, pk in retained if model is group_model}
        decision_ids = {d.pk for d in old} | {pk for model, pk in retained if model is self.model}
        decision_ids.update(system_queryset(evidence_model).filter(
            pk__in=[pk for model, pk in retained if model is evidence_model],
        ).values_list("decision_id", flat=True))
        group_ids.update(system_queryset(self.model).filter(pk__in=decision_ids).values_list("group_id", flat=True))
        old_groups = []
        for pk in sorted(group_ids):
            group = lock_if_supported(system_queryset(group_model).filter(pk=pk),
                                      no_key=True, skip_locked=pk in old_ids).first()
            if group is None:
                raise RetryableDecisionError("A decision group changed or is busy; retry admission.")
            if pk in old_ids:
                old_groups.append(group)
        list(lock_if_supported(self.filter(pk__in=decision_ids).order_by("pk"), no_key=True))
        for model, pk in retained:
            if lock_if_supported(system_queryset(model).filter(pk=pk), no_key=True).first() is None:
                raise ValidationError("An evidence record no longer exists.")
        return old_groups

    def _admit_seat(self, group: Any, index: int, item: _Admission, issuer: Any) -> Any:
        request = item.request
        requester = issuer if request.requester is DEFAULT_REQUESTER else request.requester
        decision = self.create(group=group, index=index, kind=request.kind,
            requester=None if requester is None else _user(requester), form_schema=item.schema,
            subject_content_type=item.subject[0], subject_object_id=item.subject[1], basis=request.basis,
            context=request.context.model_dump(mode="json"), errors=request.errors, supersede=request.supersede,
            max_attempts=request.attempt_limit,
            expires_at=Now() + item.expires_after if item.expires_after is not None else request.expires_at)
        decision.assignees.set(item.assignees)
        if self.filter(pk=decision.pk).due().exists():
            raise ValidationError({"expires_at": "A decision deadline must be in the future."})
        if not any(decision.with_actor(person).has_access("act") for person in item.assignees):
            raise ValidationError({"assignees": "Every seat needs an assignee who can act."})
        evidence_model = apps.get_model("decisions", "DecisionEvidence")
        evidence_model.objects.bulk_create([
            evidence_model(decision=decision, content_type=ct, object_id=pk)
            for ct, pk in sorted(item.targets, key=lambda target: (target[0].pk, target[1]))
        ])
        return decision

    def _publish(self, decision: Any) -> None:
        decision.refresh_from_db()
        publish_change(decision, action="update", update_fields=None)

    def _settle(self, group: Any) -> None:
        decisions = list(lock_if_supported(group.decisions.order_by("index"), no_key=True))
        if group.settled_at is None and group.is_settled_by(decisions):
            for decision in decisions:
                if self.filter(pk=decision.pk).close(ClosedReason.SIBLING_SETTLED):
                    self._publish(decision)
            type(group).objects.filter(pk=group.pk).settle()
            self._publish(group)
            outcome = group.outcome
            transaction.on_commit(lambda: decision_group_settled.send_robust(
                sender=type(group), group=group, outcome=outcome,
            ))

    def decide(self, decision_id: Any, *, actor: Any, revision: int, action: str, values: dict[str, Any]) -> Any:
        """Record a valid answer once; rejected forms consume a durable attempt."""
        resolver = _user(actor)
        error = None
        with self.hold(decision_id) as (group, decision):
            decision.require_access("act", resolver)
            if not decision.is_pending or decision.revision != revision or group.settled_at is not None:
                raise ValidationError({"revision": "The decision has changed; reload it."})
            target = self.filter(pk=decision.pk)
            if target.due().close(ClosedReason.EXPIRED):
                error = ValidationError({"revision": "The decision has expired."})
            else:
                try:
                    verdict, resolution = validate_form(decision.form_schema, action, values, actor=resolver)
                except ValidationError as invalid:
                    target.reject_attempt()
                    error = invalid
                else:
                    if not target.resolve(
                        revision=revision, verdict=verdict, resolution=resolution, resolver_id=resolver.pk,
                    ):
                        target.due().close(ClosedReason.EXPIRED)
                        error = ValidationError({"revision": "The decision has expired."})
            self._publish(decision)
            self._settle(group)
        if error is not None:
            raise error
        return decision.with_actor(resolver)

    def cancel_group(self, group_id: Any) -> int:
        """Cancel pending seats for a trusted waiter without changing final answers."""
        group_model = self.model._meta.get_field("group").related_model
        with group_model.objects.hold(group_id) as group:
            if group.settled_at is not None:
                return 0
            decisions = list(lock_if_supported(group.decisions.pending().order_by("pk"), no_key=True))
            count = group.decisions.close(ClosedReason.CANCELED)
            for decision in decisions:
                self._publish(decision)
            self._settle(group)
            return count

    def expire_due(self) -> int:
        """Close up to 100 due seats, rechecking deadlines under each group lock."""
        candidates = list(system_queryset(self.model).due().order_by("expires_at", "pk")
                          .values_list("pk", flat=True)[:100])
        count = 0
        for pk in candidates:
            try:
                with self.hold(pk, skip_locked=True) as (group, decision):
                    if group is not None and self.filter(pk=pk).due().close(ClosedReason.EXPIRED):
                        self._publish(decision)
                        self._settle(group)
                        count += 1
            except Exception:
                logger.exception("Decision expiry failed for %s.", pk)
        return count

    def resolutions(
        self, group_id: Any, *, actor: Any, actions: Sequence[type[Action]], basis_model: Any = None,
    ) -> list[ResolvedDecision]:
        """Lock settled seats, recheck resolver authority and parse through supplied action models."""
        actor = _user(actor)
        group_model = self.model._meta.get_field("group").related_model
        with group_model.objects.hold(group_id) as group:
            actor = group.require_access("read", actor)
            if group.settled_at is None:
                raise ValidationError("The decision group is still open.")
            return [self._read_resolution(decision, actor, actions, basis_model)
                    for decision in lock_if_supported(group.decisions.order_by("index"), no_key=True)]

    def _read_resolution(
        self, decision: Any, actor: Any, actions: Sequence[type[Action]], basis_model: Any,
    ) -> ResolvedDecision:
        decision.require_access("read", actor)
        answer = None
        resolver = None
        if decision.closed_reason == ClosedReason.RESOLVED:
            try:
                resolver = _user(decision.resolved_by)
                decision.require_access("act", resolver)
            except PermissionDenied as error:
                raise ResolverAuthorityError("The resolver no longer has authority to answer this decision.") from error
            value = decision.resolution["action"]
            action_model = next((cls for cls in actions if cls.value == value), None)
            if action_model is None:
                raise ValidationError({"action": "The action model is unavailable."})
            _, payload = validate_form(decision.form_schema, value, {
                k: v for k, v in decision.resolution.items() if k != "action"
            }, actor=resolver)
            answer = ImplBase.parse_value(
                {k: v for k, v in payload.items() if k != "action"}, action_model, "resolution",
            )
        basis = ImplBase.parse_value(decision.basis, basis_model, "basis")
        return ResolvedDecision(decision, answer, resolver, basis)

    def resolution(
        self, decision_id: Any, *, actor: Any, actions: Sequence[type[Action]], basis_model: Any = None,
    ) -> ResolvedDecision:
        """Revalidate one retained answer without requiring access to sibling seats."""
        actor = _user(actor)
        with self.hold(decision_id) as (group, decision):
            if group.settled_at is None:
                raise ValidationError("The decision group is still open.")
            return self._read_resolution(decision, actor, actions, basis_model)


class DecisionEvidenceQuerySet(AppendOnlyQuerySet[Any], AngeeQuerySet):
    """Keep evidence projections immutable while their owning group is retained."""

    def for_records(self, records: Sequence[Any]) -> Any:
        """Select retained evidence pointing to any canonical record identity."""
        targets = {canonical_record_target(record) for record in records if isinstance(record.pk, int)}
        predicate = Q(pk__in=[])
        for content_type, pk in targets:
            predicate |= Q(content_type=content_type, object_id=pk)
        return self.filter(predicate)

    def protect_record(self, instance: Any) -> None:
        """After DELETE waits on admission, check retention once and roll deletion back."""
        if not isinstance(instance.pk, int):
            return
        model = canonical_record_model(type(instance))
        with system_context(reason="decisions.protect_evidence"):
            if self.filter(content_type__app_label=model._meta.app_label,
                           content_type__model=model._meta.model_name, object_id=instance.pk).exists():
                raise ProtectedError("This record is retained as decision evidence.", [instance])


DecisionEvidenceManager = AngeeManager.from_queryset(DecisionEvidenceQuerySet)
