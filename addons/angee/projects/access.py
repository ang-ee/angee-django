"""Persist live project-container bindings and gate the project's home folder."""

from __future__ import annotations

from typing import Any

from django.apps import apps
from django.core.exceptions import ValidationError
from rebac import PermissionDenied, generic_target

from angee.base.permissions import effective_rebac_definition


def require_binding_access(*, target: Any, project: Any) -> None:
    """Require authority over both sides before a project's home folder widens access.

    The generic resource bindings carry this rule in ``projects/project_binding``
    itself; the project's ``folder`` foreign key reaches the folder through the
    same grant and keeps this explicit check.
    """

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
    """Idempotently persist one canonical project-container binding under the actor.

    The binding's ``create`` requires share on the project and the resource
    type's own grant authority through the relation ``projects/project_binding``
    declares for it; a type no relation names is refused.
    """

    binding_model = apps.get_model("projects", "ProjectBinding")
    if project.pk is None or target.pk is None:
        raise ValidationError("A project binding requires saved project and target rows.")
    binding, _created = binding_model.objects.get_or_create(
        project=project, **generic_target(target).lookups(binding_model, "target"),
    )
    return binding


def unbind(*, target: Any, project: Any) -> None:
    """Remove one explicit binding; its ``delete`` requires the same authority as ``bind``."""

    binding_model = apps.get_model("projects", "ProjectBinding")
    binding_model.objects.filter(project=project, **generic_target(target).lookups(binding_model, "target")).delete()
