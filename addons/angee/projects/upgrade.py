"""Refuse the record-task cutover before ``migrate`` runs it on a composition that cannot hold its tasks."""

from __future__ import annotations

from typing import Any

from django.apps import apps as installed
from django.core.management.base import CommandError
from django.db.models import Count
from rebac.resources import model_resource_type

from angee.base.permissions import effective_rebac_definition, permission_subject_types

RECORD_ARMS = ("record_read", "record_write")


def refuse_undeclared_record_types(state_apps: Any, using: str) -> None:
    """Refuse while a released activity sits on a record type that takes no record tasks.

    A record task answers to its record alone, so an activity on a record type
    whose owner declares no ``record_read`` and ``record_write`` on
    ``messaging/thread_attachment`` would become a task only administrators reach.
    The declarations are a fact of the current composition (its schema sources on
    disk); ``state_apps`` holds the released activities ``migrate`` is about to convert.
    """

    counts = list(
        state_apps.get_model("messaging", "ThreadActivity")._base_manager.using(using).order_by()
        .values_list("attachment__content_type__app_label", "attachment__content_type__model")
        .annotate(activities=Count("pk"), assignees=Count("user", distinct=True))
    )
    if not counts:
        return
    definition = effective_rebac_definition(installed.get_model("messaging", "ThreadAttachment"))
    reached = [
        permission_subject_types(definition, arm) if definition is not None else set() for arm in RECORD_ARMS
    ]
    undeclared = []
    for app_label, model_name, activities, assignees in counts:
        try:
            resource_type = model_resource_type(installed.get_model(app_label, model_name))
        except LookupError:
            resource_type = None
        if any(resource_type not in types for types in reached):
            undeclared.append(f"{app_label}.{model_name} ({activities} activities, {assignees} assignees)")
    if undeclared:
        raise CommandError(
            "record activities sit on record types that take no record tasks: " + "; ".join(undeclared)
            + ". A record task answers to its record alone, so these would reach only administrators. "
            "Declare record_read and record_write for these types on messaging/thread_attachment in their "
            "owner's permissions.extends.zed (or delete the activities), then migrate again."
        )


def refuse_incomplete_cutover(
    sender: Any, plan: Any = None, apps: Any = None, using: str = "default", **kwargs: Any,
) -> None:
    """``pre_migrate``: run the refusal once when the plan applies projects' record-task cutover."""

    if apps is None or not any(
        migration.app_label == "projects" and migration.name.endswith("_record_tasks") and not backwards
        for migration, backwards in plan or ()
    ):
        return
    try:
        apps.get_model("messaging", "ThreadActivity")
    except LookupError:
        return
    refuse_undeclared_record_types(apps, using)
