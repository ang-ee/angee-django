"""Fresh-process host for emitted-model resource and compatibility checks.

Run this file directly so Django constructs the real generated models without
sharing pytest's hand-built source-addon models or global app registry. Only the
temporary runtime directory is written; this host uses an in-memory database and
never runs migrations or loads resource rows.
"""

from __future__ import annotations

import argparse
import json
import os
import runpy
import sys
from pathlib import Path
from typing import Any


def boot(source_root: Path, runtime_dir: Path, extra_addon_dirs: list[Path]) -> None:
    """Compose all local manifests through the public settings and app lifecycle."""

    addon_dirs = [source_root / "addons", source_root / "examples" / "addons", *extra_addon_dirs]
    sys.path[:0] = [str(source_root), *(str(path) for path in addon_dirs)]
    for name in tuple(os.environ):
        if name.startswith("ANGEE_") or name in {"DJANGO_SETTINGS_MODULE", "DATABASE_URL"}:
            os.environ.pop(name)

    import django
    from django.conf import settings
    from hatch_angee import discover

    from angee.compose.composer import Composer

    namespace = runpy.run_module(
        "angee.compose.defaults",
        init_globals={
            "BASE_DIR": runtime_dir.parent,
            "SECRET_KEY": "isolated-composition-check",
            "ANGEE_RUNTIME_DIR": runtime_dir,
            "ANGEE_ADDON_DIRS": tuple(addon_dirs),
            "INSTALLED_APPS": [manifest.name for _, manifest in discover(addon_dirs)],
            "DATABASES": {"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": ":memory:"}},
        },
    )
    namespace = {name: value for name, value in namespace.items() if name.isupper() and not name.startswith("_")}
    settings.configure(**namespace)
    Composer(namespace).compose_settings()
    for name, value in namespace.items():
        setattr(settings, name, value)
    django.setup()


def resource_values() -> dict[str, Any]:
    """Check declared literals using each real emitted model's field instance."""

    from django.apps import apps
    from django.core.exceptions import FieldDoesNotExist, ValidationError

    from angee.addons import is_angee_addon
    from angee.resources.entries import GRANT_KIND, ResourceEntry, resource_manifest_for

    failures: list[str] = []
    checked_values = 0
    for addon in apps.get_app_configs():
        if not is_angee_addon(addon):
            continue
        for tier, declarations in sorted(resource_manifest_for(addon).items()):
            for declaration in declarations:
                entry = ResourceEntry.from_declaration(addon, tier, declaration)
                if entry.kind == GRANT_KIND:
                    continue
                for group in entry.read_groups():
                    try:
                        model = apps.get_model(group.model_label)
                    except LookupError:
                        model = None
                    for row in group.dataset.dict:
                        xref = row.pop("_xref")
                        prefix = f"{entry.display} [{entry.tier}] row {xref!r}"
                        if model is None:
                            failures.append(f"{prefix}: targets unknown model {group.model_label!r}")
                            continue
                        for name, value in row.items():
                            try:
                                model_field = model._meta.get_field(name)
                            except FieldDoesNotExist as error:
                                failures.append(f"{prefix}: {error}")
                                continue
                            if model_field.is_relation:
                                continue
                            checked_values += 1
                            if value is None and model_field.has_default():
                                value = model_field.get_default()
                            try:
                                model_field.to_python(value)
                            except ValidationError as error:
                                failures.append(
                                    f"{prefix}: {name}={value!r} rejected by "
                                    f"{type(model_field).__name__}: {error.messages}"
                                )
    return {"checked_values": checked_values, "failures": failures}


def model_snapshot() -> dict[str, Any]:
    """Expose final Django state, manager selection and tracking for comparisons."""

    import reversion
    from django.apps import apps
    from django.db.migrations.state import ModelState
    from django.db.migrations.writer import MigrationWriter

    from angee.addons import is_angee_addon

    snapshot: dict[str, Any] = {}
    for addon in apps.get_app_configs():
        if not is_angee_addon(addon):
            continue
        for model in addon.get_models():
            state = ModelState.from_model(model)
            manager = type(model._default_manager)
            snapshot[model._meta.label_lower] = {
                "fields": {name: MigrationWriter.serialize(field)[0] for name, field in state.fields.items()},
                "options": {name: MigrationWriter.serialize(value)[0] for name, value in state.options.items()},
                "bases": [str(base) for base in state.bases],
                "state_managers": MigrationWriter.serialize(state.managers)[0],
                "ordering": MigrationWriter.serialize(model._meta.ordering)[0],
                "get_latest_by": MigrationWriter.serialize(model._meta.get_latest_by)[0],
                "manager": f"{manager.__module__}.{manager.__qualname__}",
                "manager_queryset": type(model._default_manager.get_queryset()).__qualname__,
                "parents": {
                    parent._meta.label_lower: field.name if field else None
                    for parent, field in model._meta.parents.items()
                },
                "history": getattr(getattr(model, "history", None), "model", None)._meta.label_lower
                if hasattr(getattr(model, "history", None), "model") else None,
                "revision": reversion.is_registered(model),
                "checks": [{"id": issue.id, "message": str(issue)} for issue in model.check()],
            }
    return snapshot


def main() -> None:
    """Write one JSON report after the fresh host has completed Django startup."""

    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--runtime-dir", type=Path, required=True)
    parser.add_argument("--addon-dir", type=Path, action="append", default=[])
    parser.add_argument("--action", choices=("resources", "snapshot", "state"), default="resources")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    boot(args.source_root.resolve(), args.runtime_dir.resolve(), [path.resolve() for path in args.addon_dir])
    if args.action == "state":
        from django.apps import apps
        from django.db.migrations.state import ProjectState
        from django.db.migrations.writer import MigrationWriter

        states = {
            key: (state.name, state.fields, state.options, state.bases, state.managers)
            for key, state in ProjectState.from_apps(apps).models.items()
        }
        serialized, imports = MigrationWriter.serialize(states)
        args.output.write_text(
            "from django.db.migrations.state import ModelState, ProjectState\n"
            + "\n".join(sorted(imports))
            + "\nSTATE = ProjectState({key: ModelState(key[0], *values) for key, values in ("
            + serialized + ").items()})\n"
        )
        return
    result = resource_values() if args.action == "resources" else model_snapshot()
    args.output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")


if __name__ == "__main__":
    main()
