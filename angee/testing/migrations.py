"""Replay addon runtime migrations' rounds from a given state, through the composer's own loop."""

from __future__ import annotations

import tempfile
from collections.abc import Iterable
from pathlib import Path
from types import ModuleType

from django.apps import apps
from django.db.migrations.state import ProjectState

from angee.compose.migrations import RuntimeMigrations


def replay_rounds(state: ProjectState, *, addons: Iterable[str], applied: Iterable[str] = ()) -> dict[ModuleType, int]:
    """The round each declaration of the installed ``addons`` lands in from ``state``.

    Plans through ``RuntimeMigrations.schedule``, the loop the composer materializes
    with, keyed by each declaration's source module. ``applied`` names the origins
    (``"<addon>:<name>"``) a database at ``state`` already holds; they and any
    declaration that never becomes applicable are absent.
    """

    names = set(addons)
    configs = [config for config in apps.get_app_configs() if config.name in names]
    with tempfile.TemporaryDirectory() as runtime_dir:
        migrations = RuntimeMigrations(configs, runtime_dir=Path(runtime_dir), labels=apps.app_configs)
        scheduled = migrations.schedule(state, applied=applied)
    return {entry.module: entry.round for entry in scheduled}
