"""Declarative GraphQL surface for direct record sharing."""

from __future__ import annotations

import strawberry
from django.db import transaction
from rebac import SubjectRef, resolve_subjects
from rebac.resources import model_for_resource_type

from angee.base.models import AngeeModel, DirectRecordAccess
from angee.graphql.actions import ActionResult, action_guard, authorized_action_target
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
    def from_direct(
        cls,
        target_id: PublicID,
        access: DirectRecordAccess,
        label: str,
    ) -> RecordAccessType:
        """Project one model-owned direct tuple onto the GraphQL boundary."""

        return cls(
            target_id=target_id,
            relation=access.relation,
            subject=str(access.subject),
            subject_type=access.subject.subject_type,
            label=label,
        )


@strawberry.type
class RecordAccessQuery:
    """Direct-access listing for one declaratively shareable record."""

    @strawberry.field
    def record_access(
        self,
        info: strawberry.Info,
        target_type: str,
        target_ids: list[PublicID],
    ) -> list[RecordAccessType]:
        """Return direct declared-relation tuples, without effective expansion."""

        model = _shareable_model(target_type)
        permissions = sorted(set(model.get_rebac_grantable().values()))
        if not permissions:
            raise ValueError(f"{model._meta.label} declares no grantable relations.")
        if not target_ids:
            raise ValueError("Record access requires at least one target id.")
        targets = [
            (target_id, authorized_action_target(info, model, target_id, permissions[0]))
            for target_id in target_ids
        ]
        accesses = [
            (target_id, access)
            for target_id, target in targets
            for access in target.direct_record_access()
        ]
        subjects = resolve_subjects(access.subject for _, access in accesses)
        return [
            RecordAccessType.from_direct(
                target_id,
                access,
                str(subjects.get(access.subject, access.subject)),
            )
            for target_id, access in accesses
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
                authorized_action_target(info, model, target_id, permission)
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
        subject_ref = SubjectRef.parse(subject)
        with transaction.atomic():
            targets = [
                authorized_action_target(info, model, target_id, permission)
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
    """Return one concrete, existing subject for a new direct grant.

    Subject existence is boundary validation. The REBAC relationship writer
    remains the owner of whether the target relation accepts this subject type
    and optional subject-set relation.
    """

    subject = SubjectRef.parse(value)
    if subject.subject_id == "*":
        raise ValueError("Record access grants require a concrete subject.")
    if subject not in resolve_subjects((subject,)):
        raise ValueError(f"Subject {subject!s} was not found.")
    return subject


schemas = {
    "console": {
        "query": [RecordAccessQuery],
        "mutation": [RecordAccessMutation],
        "types": [RecordAccessType],
    }
}
"""Direct record-sharing contributions to the console schema."""
