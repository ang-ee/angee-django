"""Settings-backed contributors to the work task-merge transaction."""

from __future__ import annotations

from collections.abc import Iterable
from typing import Any

from django.conf import settings
from django.core import checks
from django.core.exceptions import ImproperlyConfigured
from django.utils.module_loading import import_string

MERGE_CONTRIBUTORS_SETTING = "ANGEE_WORK_MERGE_CONTRIBUTORS"
"""Autoconfig-append registry of dotted task-merge contributor callables."""


def task_merge_contributor_paths() -> tuple[str, ...]:
    """Return unique configured contributor paths in deterministic order."""

    configured = getattr(settings, MERGE_CONTRIBUTORS_SETTING, ())
    if isinstance(configured, str) or not isinstance(configured, Iterable):
        raise ImproperlyConfigured(
            f"{MERGE_CONTRIBUTORS_SETTING} must be an iterable of dotted paths."
        )
    return tuple(sorted({str(path) for path in configured}))


def run_task_merge_contributors(source: Any, canonical: Any) -> None:
    """Invoke every registered contributor inside the caller's transaction.

    Each callable receives ``(source, canonical)`` locked Task instances. It
    must move only the rows it owns, be idempotent for that exact pair, and let
    every exception propagate. The work owner invokes this only after its base
    relation/stage/link/follower postconditions and before the surrounding atomic
    block commits, so one contributor failure rolls the entire merge back.
    """

    for path in task_merge_contributor_paths():
        contributor = import_string(path)
        if not callable(contributor):
            raise ImproperlyConfigured(
                f"{MERGE_CONTRIBUTORS_SETTING} entry {path!r} is not callable."
            )
        contributor(source, canonical)


@checks.register(checks.Tags.models)
def check_task_merge_contributors(
    app_configs: list[object] | None = None,
    **kwargs: object,
) -> list[checks.CheckMessage]:
    """Fail Django checks for malformed task-merge contributor entries."""

    del app_configs, kwargs
    try:
        paths = task_merge_contributor_paths()
    except ImproperlyConfigured as error:
        return [checks.Error(str(error), id="angee.work.E001")]
    errors: list[checks.CheckMessage] = []
    for path in paths:
        try:
            contributor = import_string(path)
        except (AttributeError, ImportError, ModuleNotFoundError) as error:
            errors.append(
                checks.Error(
                    f"{MERGE_CONTRIBUTORS_SETTING} entry {path!r} cannot be imported: {error}",
                    id="angee.work.E002",
                )
            )
            continue
        if not callable(contributor):
            errors.append(
                checks.Error(
                    f"{MERGE_CONTRIBUTORS_SETTING} entry {path!r} is not callable.",
                    id="angee.work.E003",
                )
            )
    return errors
