"""Refuse the record-task cutover before ``migrate`` runs it on a composition that cannot hold its tasks."""

from __future__ import annotations

from typing import Any

from django.apps import apps as installed
from django.core import checks
from django.core.management.base import CommandError
from django.db.models import Count
from django.db.models.signals import pre_migrate
from rebac.resources import model_resource_type
from rebac.schema.introspection import permission_sources

from angee.base.permissions import effective_rebac_schema
from angee.base.runtime_migrations import runtime_migration_origin

CUTOVER_ORIGIN = "angee.projects:record_tasks"
"""The cutover's runtime-migration origin, as projects' addon.toml declares it."""

GUARD_DISPATCH_UID = "projects.record_tasks.refuse_incomplete_cutover"
RECORD_ARMS = ("record_read", "record_write")


def record_task_types() -> set[str]:
    """Return the record types the current composition lets take record tasks.

    The record arms on ``messaging/thread_attachment`` are unions of arrows through
    its target relations (messaging's contract), so a type takes record tasks
    when both arms reach it through some arrow. The schema is the composition's
    sources on disk, not the database's synced copy.
    """

    attachment = installed.get_model("messaging", "ThreadAttachment")
    resource_type = model_resource_type(attachment)
    schema = effective_rebac_schema(installed.get_app_config(attachment._meta.app_label))
    definition = schema.get_definition(resource_type) if schema is not None and resource_type else None
    if definition is None:
        return set()
    relations = {relation.name: relation for relation in definition.relations}
    reached = [
        {
            subject.type
            for via, _target in permission_sources(schema, resource_type, arm).arrows
            if via in relations
            for subject in relations[via].allowed_subjects
        }
        for arm in RECORD_ARMS
    ]
    return set.intersection(*reached)


def refuse_undeclared_record_types(state_apps: Any, using: str) -> None:
    """Refuse while a released activity sits on a record type that takes no record tasks.

    A record task answers to its record alone, so an activity on a record type
    whose owner declares no ``record_read`` and ``record_write`` on
    ``messaging/thread_attachment`` would become a task only administrators reach.
    The declarations are a fact of the current composition (:func:`record_task_types`);
    ``state_apps`` holds the released activities ``migrate`` is about to convert.
    """

    counts = list(
        state_apps.get_model("messaging", "ThreadActivity")._base_manager.using(using).order_by()
        .values_list("attachment__content_type__app_label", "attachment__content_type__model")
        .annotate(activities=Count("pk"), assignees=Count("user", distinct=True))
    )
    if not counts:
        return
    declared = record_task_types()
    undeclared = []
    for app_label, model_name, activities, assignees in counts:
        try:
            resource_type = model_resource_type(installed.get_model(app_label, model_name))
        except LookupError:
            resource_type = None
        if resource_type not in declared:
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
    """``pre_migrate``: run the refusal once when the plan applies projects' record-task cutover.

    The cutover is recognized by the origin its materialized migration records.
    ``migrate --fake`` is refused the same way: the signal does not say whether
    the plan is faked, and a faked cutover would leave the activities unconverted
    in tables nothing reads any more.
    """

    if apps is None or not any(
        runtime_migration_origin(migration) == CUTOVER_ORIGIN and not backwards
        for migration, backwards in plan or ()
    ):
        return
    try:
        apps.get_model("messaging", "ThreadActivity")
    except LookupError:
        return
    refuse_undeclared_record_types(apps, using)


def check_cutover_guard(app_configs: Any = None, **kwargs: Any) -> list[checks.CheckMessage]:
    """Report a process whose ``migrate`` would apply the cutover unguarded.

    The cutover migration holds history only; :func:`refuse_incomplete_cutover`,
    connected to ``pre_migrate`` for projects, is its sole guard. The check runs
    before a manual ``manage.py migrate``; ``angee provision`` migrates with
    ``--skip-checks`` and checks afterwards, so there it reports a disconnected
    guard only once the cutover has run.
    """

    if any(lookup_key[0] == GUARD_DISPATCH_UID for lookup_key, *_receiver in pre_migrate.receivers):
        return []
    return [checks.Error(
        "The record-task cutover's pre_migrate guard is not connected.",
        hint="angee.projects.signals.connect() connects it; ProjectsConfig.ready() must call it.",
        id="projects.E001",
    )]
