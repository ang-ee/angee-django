"""Explicit installation ownership persisted through the resource ledger."""

from __future__ import annotations

from typing import Any

from django.apps import apps
from django.contrib.auth import get_user_model
from django.db import models, transaction
from rebac import backend as rebac_backend
from rebac import system_context, to_subject_ref

from angee.iam.roles import platform_admin_role
from angee.resources.exceptions import ResourceLoadError
from angee.resources.signals import post_load, pre_load

DEPLOYMENT_OPERATOR_XREF = "deployment_operator"
"""IAM-local handle; resource documents refer to ``iam.deployment_operator``."""

_REMEDY = "Run bootstrap_admin for an unbound installation, or rebind_deployment_operator --username OPERATOR."


def _ledger(ledger_model: type[models.Model]) -> Any:
    return ledger_model._default_manager.filter(
        source_addon=apps.get_app_config("iam").name, xref=DEPLOYMENT_OPERATOR_XREF,
    )


def _validate_operator(user: Any, *, require_role: bool) -> None:
    if not user.is_person or not user.is_active:
        raise ResourceLoadError("Deployment operator must be an active human. " + _REMEDY)
    if require_role:
        role = platform_admin_role()
        subject = to_subject_ref(user)
        # One native lookup covers direct, inherited and group-held memberships.
        holders = () if role is None else rebac_backend().lookup_subjects(
            resource=role, action="effective_member", subject_type=subject.subject_type,
        )
        if not any(holder.subject_id in (subject.subject_id, "*") for holder in holders):
            raise ResourceLoadError("Deployment operator must hold the platform-admin role. " + _REMEDY)


def bind_deployment_operator(user: Any) -> Any | None:
    """Bind bootstrap's administrator only if unbound; preserve recovery bootstraps."""

    model = apps.get_model("resources", "Resource")
    with system_context(reason="iam.deployment_operator.bootstrap"), transaction.atomic():
        existing = _ledger(model).first()
        if existing is not None:
            return existing.target_instance()
        _validate_operator(user, require_role=True)
        try:
            with transaction.atomic():
                model.objects.bind_instance(
                    addon=apps.get_app_config("iam"), xref=DEPLOYMENT_OPERATOR_XREF,
                    instance=user, source="deployment_operator",
                )
        except ResourceLoadError:
            # A concurrent bootstrap may have filled the unique handle. Keep
            # that choice while allowing this rescue administrator to survive.
            existing = _ledger(model).first()
            if existing is None:
                raise
            return existing.target_instance()
        return user


def rebind_deployment_operator(user: Any) -> None:
    """Explicitly replace the installation's operator through the ledger owner."""

    with system_context(reason="iam.deployment_operator.rebind"), transaction.atomic():
        _validate_operator(user, require_role=True)
        apps.get_model("resources", "Resource").objects.bind_instance(
            addon=apps.get_app_config("iam"), xref=DEPLOYMENT_OPERATOR_XREF,
            instance=user, source="deployment_operator", replace=True,
        )


def resolve_deployment_operator(*, ledger_model: type[models.Model], require_role: bool = True) -> Any:
    """Resolve the explicit handle; never infer an operator from administrator count."""

    with system_context(reason="iam.deployment_operator.resolve"):
        ledger = _ledger(ledger_model).first()
        if ledger is None:
            raise ResourceLoadError("Deployment operator is unbound. " + _REMEDY)
        if ledger.target_model.lower() != get_user_model()._meta.label_lower:
            raise ResourceLoadError("Deployment operator must identify an IAM user. " + _REMEDY)
        operator = ledger.target_instance()
        if operator is None:
            raise ResourceLoadError("Deployment operator has a missing target. " + _REMEDY)
        _validate_operator(operator, require_role=require_role)
        return operator


def _validate_references(
    sender: type[models.Model], *, referenced_handles: frozenset[tuple[str, str]], **kwargs: Any,
) -> None:
    if (apps.get_app_config("iam").name, DEPLOYMENT_OPERATOR_XREF) in referenced_handles:
        resolve_deployment_operator(ledger_model=sender, require_role=False)


def _validate_loaded_role(
    sender: type[models.Model], *, referenced_handles: frozenset[tuple[str, str]], **kwargs: Any,
) -> None:
    if (apps.get_app_config("iam").name, DEPLOYMENT_OPERATOR_XREF) in referenced_handles:
        resolve_deployment_operator(ledger_model=sender)


def connect() -> None:
    """Wire pre-load identity checks and post-grant role checks through Django."""

    pre_load.connect(_validate_references, dispatch_uid="angee.iam.operator_identity", weak=False)
    post_load.connect(_validate_loaded_role, dispatch_uid="angee.iam.operator_role", weak=False)
