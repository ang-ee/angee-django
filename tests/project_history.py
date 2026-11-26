"""A project's own migration history in the bare test host, so a template is applied as a host applies it.

A host copies a migration template into ``runtime/<label>/migrations/``, pins its
placeholder dependencies to its own leaves and keeps its bytes. ``migrate``, and so
``provision``, has django-compose's drop guard judge the pending plan
(``check_plan_drops``) before any operation runs, which Django's executor alone does
not. :func:`project_history` lays such histories over the run's migration modules, in
a package under the test's ``tmp_path`` that no installed distribution owns, so the
guard judges them as the project's own. Each label starts at an applied
``0001_initial`` declaring the models a migration state gives it;
:meth:`ProjectHistory.copy` adds a template after it, pending. Start every label
whose relations the template follows: Django renders an app without a history
without its relations.
"""

from __future__ import annotations

import importlib
import inspect
import sys
from collections.abc import Iterator, Mapping
from contextlib import contextmanager
from pathlib import Path
from types import ModuleType
from typing import Any

from django.conf import settings
from django.db import connection, migrations
from django.db.migrations.executor import MigrationExecutor
from django.db.migrations.operations.base import Operation
from django.db.migrations.recorder import MigrationRecorder
from django.db.migrations.state import ProjectState
from django.test import override_settings
from django_compose.migrations import check_plan_drops

INITIAL = "0001_initial"
# Each started history's initial operations, by the module that declares them.
_DECLARED: dict[str, list[Operation]] = {}
_INITIAL_SOURCE = "from tests.project_history import initial\n\nMigration = initial(__name__)\n"


def initial(module: str) -> type[migrations.Migration]:
    """The ``Migration`` of the initial module ``module``: the operations its history was started with."""

    return type("Migration", (migrations.Migration,), {"initial": True, "operations": _DECLARED[module]})


def declared(state: ProjectState, label: str) -> list[Operation]:
    """The operations of an initial migration that declares ``state``'s models of ``label``."""

    return [
        migrations.CreateModel(
            name=model.name, fields=list(model.fields.items()), options=dict(model.options), bases=model.bases,
            managers=list(model.managers),
        )
        for (app_label, _), model in sorted(state.models.items())
        if app_label == label
    ]


class _Histories:
    """The run's migration modules, with a project's own histories laid over them.

    ``--nomigrations`` replaces the setting with an object that answers only ``in``
    and ``[]``, which is all Django's loader asks of it; this answers the same two,
    so the histories are read whichever way the run was started.
    """

    def __init__(self, configured: Any, histories: Mapping[str, str]) -> None:
        self.configured, self.histories = configured, histories

    def __contains__(self, label: object) -> bool:
        return label in self.histories or label in self.configured

    def __getitem__(self, label: str) -> str | None:
        return self.histories[label] if label in self.histories else self.configured[label]


class ProjectHistory:
    """The histories a test starts, in one package under its ``tmp_path``."""

    def __init__(self, root: Path) -> None:
        self.package = f"project_history_{root.name.replace('-', '_')}"
        self.folder = root / self.package
        self.modules: dict[str, str] = {}

    def start(self, state: ProjectState, *labels: str) -> None:
        """Begin each label's history at ``0001_initial``, recorded as applied, declaring its models in ``state``."""

        self.folder.mkdir(exist_ok=True)
        (self.folder / "__init__.py").touch()
        for label in labels:
            folder = self.folder / label
            folder.mkdir()
            (folder / "__init__.py").touch()
            _DECLARED[f"{self.package}.{label}.{INITIAL}"] = declared(state, label)
            (folder / f"{INITIAL}.py").write_text(_INITIAL_SOURCE)
            self.modules[label] = f"{self.package}.{label}"
            MigrationRecorder(connection).record_applied(label, INITIAL)
        importlib.invalidate_caches()

    def copy(
        self, label: str, template: ModuleType, name: str, pins: Mapping[str, str], *, declarations: bool = True,
    ) -> None:
        """Copy ``template``'s bytes into ``label``'s history as ``name``, each placeholder in ``pins`` pinned.

        ``pins`` maps a placeholder migration name to the history's own. Without
        ``declarations`` the copy writes each removal the template declares as
        Django's ``RemoveField``, as a copy taken before the template declared it.
        """

        source = inspect.getsource(template)
        for placeholder, pinned in pins.items():
            assert f'"{placeholder}"' in source, f"{template.__name__} names no placeholder {placeholder}"
            source = source.replace(f'"{placeholder}"', f'"{pinned}"')
        if not declarations:
            assert "IntendedRemoveField(" in source, f"{template.__name__} declares no removal"
            source = source.replace("IntendedRemoveField(", "migrations.RemoveField(")
        (self.folder / label / f"{name}.py").write_text(source)
        importlib.invalidate_caches()

    def migrate(self, label: str, name: str) -> None:
        """Migrate ``label`` to ``name`` as ``migrate`` does: the drop guard judges the plan, then it is applied."""

        executor = MigrationExecutor(connection)
        targets = [(label, name)]
        plan = executor.migration_plan(targets)
        check_plan_drops(plan, using=connection.alias)
        executor.migrate(targets, plan=plan)

    def applied(self, label: str, name: str) -> bool:
        """Whether ``label``'s ``name`` is recorded as applied."""

        return (label, name) in MigrationRecorder(connection).applied_migrations()


@contextmanager
def project_history(root: Path) -> Iterator[ProjectHistory]:
    """Lay a project's histories over the run's migration modules for the block; forget them after it."""

    history = ProjectHistory(root)
    sys.path.insert(0, str(root))
    try:
        with override_settings(MIGRATION_MODULES=_Histories(settings.MIGRATION_MODULES, history.modules)):
            yield history
    finally:
        sys.path.remove(str(root))
        for module in [module for module in sys.modules if module.partition(".")[0] == history.package]:
            del sys.modules[module]
        for label in history.modules:
            _DECLARED.pop(f"{history.package}.{label}.{INITIAL}", None)
        MigrationRecorder(connection).migration_qs.filter(app__in=list(history.modules)).delete()
        importlib.invalidate_caches()
