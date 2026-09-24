"""Declarative GraphQL surface for direct record sharing."""

from __future__ import annotations

import strawberry
from django.core.exceptions import ValidationError
from django.db import transaction
from rebac import PermissionDenied, SubjectRef, resolve_subjects
from rebac.resources import model_for_resource_type

from angee.base.identity import canonical_subject_ref, public_subject_ref
from angee.base.models import AngeeModel, DirectRecordAccess
from angee.graphql.actions import ActionResult, action_guard, authorized_permission_target
from angee.graphql.ids import PublicID


@strawberry.type
class RecordAccessType:
    """One stored direct relation on a record's declared share surface."""

    target_id: PublicID
    relation: str
    subject: str
    subject_type: str
    label: str

    @classmethod
    def from_direct(cls, target_id: PublicID, access: DirectRecordAccess, label: str) -> RecordAccessType:
        """Project one model-owned direct tuple onto the GraphQL boundary."""

        return cls(
            target_id=target_id,
            relation=access.relation,
            subject=str(public_subject_ref(access.subject)),
            subject_type=access.subject.subject_type,
            label=label,
        )


@strawberry.type
class RecordAccessOption:
    """One direct relation the caller may manage on the target record."""

    relation: str
    permission: str


@strawberry.type
class RecordAccessQuery:
    """Direct-access listing for declaratively shareable records."""

    @strawberry.field
    def record_access(
        self, info: strawberry.Info, target_type: str, target_ids: list[PublicID],
    ) -> list[RecordAccessType]:
        """Return direct declared-relation tuples, without effective expansion."""

        model = _shareable_model(target_type)
        if not target_ids:
            raise ValueError("Record access requires at least one target id.")
        accesses: list[tuple[PublicID, DirectRecordAccess]] = []
        for target_id in target_ids:
            target, allowed = _authorized_record_access(info, model, target_id)
            accesses.extend((target_id, access) for access in target.direct_record_access(allowed))
        subjects = resolve_subjects(access.subject for _, access in accesses)
        return [
            RecordAccessType.from_direct(target_id, access, str(subjects.get(access.subject, access.subject)))
            for target_id, access in accesses
        ]

    @strawberry.field
    def record_access_options(
        self, info: strawberry.Info, target_type: str, target_ids: list[PublicID],
    ) -> list[RecordAccessOption]:
        """Return relations the caller may manage on every selected record."""

        model = _shareable_model(target_type)
        if not target_ids:
            raise ValueError("Record access options require at least one target id.")
        declaration = model.get_rebac_grantable()
        allowed = set(declaration)
        for target_id in dict.fromkeys(target_ids):
            _target, target_allowed = _authorized_record_access(info, model, target_id)
            allowed.intersection_update(target_allowed)
        return [
            RecordAccessOption(relation=relation, permission=permission)
            for relation, permission in declaration.items()
            if relation in allowed
        ]


@strawberry.type
class RecordAccessMutation:
    """Authored grant and revoke actions for declared record relations."""

    @strawberry.mutation
    @action_guard("Grant record access failed.")
    def grant_record_access(
        self,
        info: strawberry.Info,
        target_type: str,
        target_ids: list[PublicID],
        relation: str,
        subject: str,
    ) -> ActionResult:
        """Idempotently grant one canonical subject across the selected records."""

        model = _shareable_model(target_type)
        if not target_ids:
            raise ValueError("Granting record access requires at least one target id.")
        permission = model.record_access_permission(relation)

        with transaction.atomic():
            targets = [
                authorized_permission_target(info, model, target_id, permission)
                for target_id in target_ids
            ]
            subject_ref = _grant_subject(subject)
            for target in targets:
                target.grant_record_access(relation, subject_ref)
        return ActionResult(ok=True, message="Record access granted.")

    @strawberry.mutation
    @action_guard("Revoke record access failed.")
    def revoke_record_access(
        self,
        info: strawberry.Info,
        target_type: str,
        target_ids: list[PublicID],
        relation: str,
        subject: str,
    ) -> ActionResult:
        """Idempotently revoke one canonical subject across the selected records."""

        model = _shareable_model(target_type)
        if not target_ids:
            raise ValueError("Revoking record access requires at least one target id.")
        permission = model.record_access_permission(relation)
        subject_ref = canonical_subject_ref(subject)

        with transaction.atomic():
            targets = [
                authorized_permission_target(info, model, target_id, permission)
                for target_id in target_ids
            ]
            for target in targets:
                target.revoke_record_access(relation, subject_ref)
        return ActionResult(ok=True, message="Record access revoked.")


def _shareable_model(target_type: str) -> type[AngeeModel]:
    """Resolve one declared Angee record model from its REBAC resource type."""

    model = model_for_resource_type(target_type)
    if model is None or not issubclass(model, AngeeModel):
        raise ValueError(f"Record target type {target_type!r} is not shareable.")
    return model


def _grant_subject(value: str) -> SubjectRef:
    """Return one concrete, existing subject for a new direct grant."""

    subject = canonical_subject_ref(value)
    if subject.subject_id == "*":
        raise ValueError("Record access grants require a concrete subject.")
    if subject not in resolve_subjects((subject,)):
        raise ValueError(f"Subject {subject!s} was not found.")
    return subject


def _authorized_record_access(
    info: strawberry.Info,
    model: type[AngeeModel],
    target_id: PublicID,
) -> tuple[AngeeModel, list[str]]:
    """Resolve a target and independently authorize each declared share relation."""

    declaration = model.get_rebac_grantable()
    if not declaration:
        raise ValueError(f"{model._meta.label} declares no grantable relations.")
    target: AngeeModel | None = None
    allowed: list[str] = []
    candidates: dict[str, AngeeModel] = {}
    for permission in dict.fromkeys(declaration.values()):
        try:
            candidates[permission] = authorized_permission_target(info, model, target_id, permission)
        except (PermissionDenied, ValidationError):
            continue
    for relation, permission in declaration.items():
        if permission in candidates:
            target = candidates[permission]
            allowed.append(relation)
    if target is None:
        target = authorized_permission_target(info, model, target_id, next(iter(declaration.values())))
    target.validate_record_access_target()
    return target, allowed


schemas = {
    "console": {
        "query": [RecordAccessQuery],
        "mutation": [RecordAccessMutation],
        "types": [RecordAccessType, RecordAccessOption],
    }
}
