"""Replay addon runtime migrations' rounds from a given state, through the composer's own loop."""

from __future__ import annotations

import tempfile
from collections.abc import Iterable
from pathlib import Path
from types import ModuleType

from django.apps import apps
from django.db.migrations.state import ProjectState

from angee.compose.migrations import RuntimeMigrations


def replay_rounds(state: ProjectState, origins: Iterable[str]) -> dict[ModuleType, int]:
    """The round each named declaration (``"<addon>:<name>"``) lands in from ``state``.

    Plans through ``RuntimeMigrations.schedule``, the loop the composer materializes
    with, over the addons the origins name, keyed by each declaration's source module.
    A declaration that never becomes applicable is absent; an origin no installed
    addon declares fails.
    """

    only = tuple(origins)
    names = {origin.split(":", 1)[0] for origin in only}
    configs = [config for config in apps.get_app_configs() if config.name in names]
    with tempfile.TemporaryDirectory() as runtime_dir:
        migrations = RuntimeMigrations(configs, runtime_dir=Path(runtime_dir), labels=apps.app_configs)
        scheduled = migrations.schedule(state, only=only)
    return {entry.module: entry.round for entry in scheduled}
