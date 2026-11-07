"""Gate the project's home folder, whose foreign key widens folder access like a binding."""

from __future__ import annotations

from typing import Any

from rebac import PermissionDenied

from angee.base.permissions import effective_rebac_definition


def require_binding_access(*, target: Any, project: Any) -> None:
    """Require authority over both sides before a project's home folder widens access.

    Generic resource bindings carry this rule in ``projects/project_binding``
    itself (:meth:`angee.projects.models.ProjectBindingManager.bind`); the
    project's ``folder`` foreign key reaches the folder through the same grant
    and keeps this explicit check.
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
