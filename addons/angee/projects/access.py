"""Authorize and persist live project-container bindings."""

from __future__ import annotations

from typing import Any

from django.apps import apps
from django.core.exceptions import ValidationError
from django.db import transaction
from rebac import PermissionDenied, system_context

from angee.base.permissions import effective_rebac_definition
from angee.base.refs import canonical_record_target


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

    binding_model = apps.get_model("projects", "ProjectBinding")
    if project.pk is None or target.pk is None:
        raise ValidationError("A project binding requires saved project and target rows.")
    require_binding_access(project=project, target=target)
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

    binding_model = apps.get_model("projects", "ProjectBinding")
    require_binding_access(project=project, target=target)
    binding_model.validate_target(target)
    canonical = canonical_record_target(target)
    with transaction.atomic():
        with system_context(reason="projects.binding.unbind"):
            binding_model._base_manager.filter(
                project=project,
                content_type=canonical.content_type,
                object_id=canonical.object_id,
            ).delete()
