"""Projects-owned projection of container membership into REBAC relationships."""

from __future__ import annotations

from typing import Any

from django.apps import apps
from django.core.exceptions import ValidationError
from django.db import transaction
from rebac import (
    ObjectRef,
    PermissionDenied,
    RelationshipTuple,
    SubjectRef,
    delete_relationship,
    delete_relationships,
    system_context,
    to_object_ref,
    write_relationships,
)
from rebac.resources import model_resource_type
from rebac.types import RelationshipFilter

from angee.base.identity import public_id_for
from angee.base.permissions import effective_rebac_definition
from angee.base.refs import CanonicalRecordTarget, canonical_record_model, canonical_record_target

PROJECT_RELATION = "project"


def require_binding_access(*, target: Any, project: Any) -> None:
    """Require authority over both sides before project access can be widened."""

    if not project.has_access("share"):
        raise PermissionDenied("Share access to the project is required to bind a resource.")
    require_target_binding_access(target)


def require_target_binding_access(target: Any) -> None:
    """Require the target's native grant authority for an unsaved project."""

    permission = _target_binding_permission(type(target))
    if not target.has_access(permission):
        raise PermissionDenied(f"{permission.title()} access to the resource is required to bind it to a project.")


def _target_binding_permission(model: type[Any]) -> str:
    """Use the target's native share permission, falling back to its write owner."""

    definition = effective_rebac_definition(model)
    if definition is not None and any(permission.name == "share" for permission in definition.permissions):
        return "share"
    return "write"


def bind(*, target: Any, project: Any) -> Any:
    """Idempotently persist one canonical project-container binding."""

    if project.pk is None or target.pk is None:
        raise ValidationError("A project binding requires saved project and target rows.")
    require_binding_access(project=project, target=target)
    binding_model = apps.get_model("projects", "ProjectBinding")
    binding_model.validate_target(target)
    canonical = canonical_record_target(target)
    with transaction.atomic():
        with system_context(reason="projects.binding.bind"):
            binding, _ = binding_model._base_manager.get_or_create(
                project=project,
                content_type=canonical.content_type,
                object_id=canonical.object_id,
            )
    return binding


def unbind(*, target: Any, project: Any) -> None:
    """Remove one explicit binding while preserving every other evidence row."""

    require_binding_access(project=project, target=target)
    binding_model = apps.get_model("projects", "ProjectBinding")
    binding_model.validate_target(target)
    canonical = canonical_record_target(target)
    with transaction.atomic():
        with system_context(reason="projects.binding.unbind"):
            binding_model._base_manager.filter(
                project=project,
                content_type=canonical.content_type,
                object_id=canonical.object_id,
            ).delete()


def reconcile_on_commit(
    *,
    project_pk: Any,
    project_ref: Any,
    target: CanonicalRecordTarget | None,
    using: str,
) -> None:
    """Reconcile one project-target tuple from committed binding evidence."""

    if target is None:
        return
    transaction.on_commit(
        lambda: _reconcile(
            project_pk=project_pk,
            project_ref=project_ref,
            target=target,
            using=using,
        ),
        using=using,
    )


def _reconcile(
    *,
    project_pk: Any,
    project_ref: Any,
    target: CanonicalRecordTarget,
    using: str,
) -> None:
    """Write or remove the tuple after checking every projects-owned evidence row."""

    project_model = apps.get_model("projects", "Project")
    binding_model = apps.get_model("projects", "ProjectBinding")
    project_exists = project_model._base_manager.using(using).filter(pk=project_pk).exists()
    direct_folder = (
        target.content_type.app_label == "storage"
        and target.content_type.model == "folder"
        and project_model._base_manager.using(using).filter(pk=project_pk, folder_id=target.object_id).exists()
    )
    explicit_binding = binding_model._base_manager.using(using).filter(
        project_id=project_pk,
        content_type_id=target.content_type.pk,
        object_id=target.object_id,
    ).exists()
    relationship = _relationship(project_ref, target)
    if project_exists and (direct_folder or explicit_binding):
        write_relationships([relationship])
    else:
        delete_relationship(relationship)


def resync_project_access() -> int:
    """Backfill every committed Project.folder and ProjectBinding tuple.

    Run after ``rebac sync`` has loaded the projects schema revision that defines
    the additive ``project`` relations. The operation is idempotent and returns
    the number of distinct evidence pairs written.
    """

    project_model = apps.get_model("projects", "Project")
    binding_model = apps.get_model("projects", "ProjectBinding")
    relationships: set[RelationshipTuple] = set()
    with system_context(reason="projects.access.resync"), transaction.atomic():
        for project in project_model._base_manager.select_related("folder").filter(folder__isnull=False).order_by("pk"):
            relationship = _relationship(to_object_ref(project), canonical_record_target(project.folder))
            relationships.add(relationship)
        for binding in binding_model._base_manager.select_related("project", "content_type").order_by("pk"):
            relationship = _relationship(
                to_object_ref(binding.project),
                CanonicalRecordTarget(binding.content_type, binding.object_id),
            )
            relationships.add(relationship)
        for resource_type in _binding_resource_types(binding_model):
            delete_relationships(
                RelationshipFilter(
                    resource_type=resource_type,
                    relation=PROJECT_RELATION,
                    subject_type="projects/project",
                )
            )
        if relationships:
            write_relationships(sorted(relationships, key=str))
    return len(relationships)


def _relationship(project_ref: Any, target: CanonicalRecordTarget) -> RelationshipTuple:
    """Build one canonical project-container relationship from stable references."""

    target_model = target.content_type.model_class()
    if target_model is None or not (resource_type := model_resource_type(target_model)):
        raise ValueError(f"{target.content_type} is not a REBAC resource.")
    return RelationshipTuple(
        resource=ObjectRef(resource_type, public_id_for(target_model, target.object_id)),
        relation=PROJECT_RELATION,
        subject=SubjectRef(project_ref),
    )


def _binding_resource_types(binding_model: Any) -> tuple[str, ...]:
    """Compile canonical REBAC target types from the binding model's declaration."""

    resource_types = {
        resource_type
        for model_label in binding_model.allowed_target_models
        if (resource_type := model_resource_type(canonical_record_model(apps.get_model(model_label)))) is not None
    }
    return tuple(sorted(resource_types))
