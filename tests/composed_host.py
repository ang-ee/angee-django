"""Fresh-process host for emitted-model resource and compatibility checks.

Run this file directly so Django constructs the real generated models without
sharing pytest's hand-built source-addon models or global app registry. Only the
temporary runtime directory is written. Native addon tests use Django's test
runner with SQLite by default; ``--test-postgresql`` accepts the runner's
PostgreSQL DATABASE_URL and creates a separate native test database.
"""

from __future__ import annotations

import argparse
import json
import os
import runpy
import subprocess
import sys
from pathlib import Path
from typing import Any

import environ

COMPOSED_TEST_TIMEOUT = 120
"""Bound composition and native test groups, allowing headroom over measured 15–29s runs."""


def run_composed_tests(
    tmp_path: Path, test_label: str, *, app: str | tuple[str, ...], test_postgresql: bool = False,
) -> None:
    """Run a native contract group without sharing pytest's source-model registry."""

    root = Path(__file__).resolve().parents[1]
    report = tmp_path / "composed-tests.json"
    env = dict(os.environ)
    env.pop("DJANGO_SETTINGS_MODULE", None)
    result = subprocess.run(
        [
            sys.executable,
            str(root / "tests/composed_host.py"),
            "--runtime-dir",
            str(tmp_path / "runtime"),
            *(["--test-postgresql"] if test_postgresql else []),
            *(argument for name in ((app,) if isinstance(app, str) else app) for argument in ("--app", name)),
            "--no-examples",
            "--action",
            "tests",
            "--test-label",
            test_label,
            "--output",
            str(report),
        ],
        cwd=root,
        env=env,
        capture_output=True,
        text=True,
        timeout=COMPOSED_TEST_TIMEOUT,
        check=False,
    )
    assert result.returncode == 0, f"composed tests failed:\n{result.stdout}\n{result.stderr}"
    assert json.loads(report.read_text())["failures"] == 0


def boot(
    source_root: Path,
    runtime_dir: Path,
    extra_addon_dirs: list[Path],
    *,
    include_examples: bool = True,
    root_apps: list[str] | None = None,
    test_postgresql: bool = False,
) -> None:
    """Compose selected app roots and their native dependency closure."""

    database: dict[str, Any] = {"ENGINE": "django.db.backends.sqlite3", "NAME": ":memory:"}
    if test_postgresql:
        database_url = os.environ.get("DATABASE_URL", "")
        database = environ.Env.db_url_config(database_url)
        if database["ENGINE"] != "django.db.backends.postgresql":
            raise ValueError("--test-postgresql requires a PostgreSQL DATABASE_URL.")
        # DiscoverRunner creates this secondary database. A composed-host test
        # can run beside another one on a different xdist worker, so give each
        # worker its own name just as pytest-django does for the primary database.
        worker = os.environ.get("PYTEST_XDIST_WORKER")
        suffix = f"_{worker}" if worker else ""
        database["TEST"] = {"NAME": f"test_composed_{database['NAME']}{suffix}"}
    addon_dirs = [source_root / "addons"]
    if include_examples:
        addon_dirs.append(source_root / "examples" / "addons")
    addon_dirs.extend(extra_addon_dirs)
    sys.path[:0] = [str(source_root), *(str(path) for path in addon_dirs)]
    for name in tuple(os.environ):
        if name.startswith("ANGEE_") or name in {"DJANGO_SETTINGS_MODULE", "DATABASE_URL"}:
            os.environ.pop(name)

    import django
    from django.conf import settings
    from hatch_angee import discover

    from angee.compose.composer import Composer

    installed_apps = (
        list(root_apps)
        if root_apps is not None
        else [manifest.name for _, manifest in discover(addon_dirs)]
    )
    namespace = runpy.run_module(
        "angee.compose.defaults",
        init_globals={
            "BASE_DIR": runtime_dir.parent,
            "SECRET_KEY": "isolated-composition-check",
            "ANGEE_RUNTIME_DIR": runtime_dir,
            "ANGEE_ADDON_DIRS": tuple(addon_dirs),
            "INSTALLED_APPS": installed_apps,
            "DATABASES": {"default": database},
            "ANGEE_MONEY_REFERENCE_CURRENCY": "USD",
        },
    )
    namespace = {name: value for name, value in namespace.items() if name.isupper() and not name.startswith("_")}
    settings.configure(**namespace)
    Composer(namespace).compose_settings()
    for name, value in namespace.items():
        setattr(settings, name, value)
    django.setup()


def resource_values() -> dict[str, Any]:
    """Check declared literals, excluding dataset padding, with emitted fields."""

    from django.apps import apps
    from django.core.exceptions import FieldDoesNotExist, ValidationError
    from django.db.models.fields import NOT_PROVIDED

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
                            if value is NOT_PROVIDED:
                                continue
                            try:
                                model_field = model._meta.get_field(name)
                            except FieldDoesNotExist as error:
                                resource_class = getattr(model, "resource_class", None)
                                resource_field = resource_class.fields.get(name) if resource_class else None
                                if resource_field is None:
                                    failures.append(f"{prefix}: {error}")
                                else:
                                    checked_values += 1
                                    try:
                                        resource_field.clean(row)
                                    except (ValidationError, ValueError) as resource_error:
                                        failures.append(f"{prefix}: {name} rejected by resource: {resource_error}")
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
                if hasattr(getattr(model, "history", None), "model")
                else None,
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
    parser.add_argument(
        "--app",
        action="append",
        default=None,
        help="Root app to compose (repeatable); dependencies are resolved from addon manifests.",
    )
    parser.add_argument(
        "--no-examples",
        action="store_true",
        help="Exclude showcase addons from the composed host profile.",
    )
    parser.add_argument("--action", choices=("resources", "snapshot", "state", "tests", "schemas"), default="resources")
    parser.add_argument("--test-label", action="append", default=[])
    parser.add_argument("--test-postgresql", action="store_true")
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    if args.test_postgresql and args.action != "tests":
        parser.error("--test-postgresql is available only for native test runs")
    boot(
        args.source_root.resolve(),
        args.runtime_dir.resolve(),
        [path.resolve() for path in args.addon_dir],
        include_examples=not args.no_examples,
        root_apps=args.app,
        test_postgresql=args.test_postgresql,
    )
    if args.action == "tests":
        from django.apps import apps
        from django.conf import settings
        from django.core.management import call_command
        from django.db import connection
        from django.test.runner import DiscoverRunner

        class ComposedTestRunner(DiscoverRunner):
            """Prepare the generated host's schema before Django checks its models."""

            def setup_databases(self, **kwargs: Any) -> Any:
                databases = super().setup_databases(**kwargs)
                call_command("rebac", "sync", verbosity=0)
                return databases

        assert args.test_label, "Native tests require explicit test labels"
        if not args.test_postgresql:
            assert settings.DATABASES["default"]["ENGINE"] == "django.db.backends.sqlite3"
            assert settings.DATABASES["default"]["NAME"] == ":memory:"
        settings.ANGEE_GRAPHQL_ALLOW_INMEMORY_CHANNEL_LAYER = True
        # Generated apps have no migration history in this disposable host.
        # Keep REBAC's native migrations and their contenttypes dependency: the
        # library owns the schema witness required by its cached evaluator.
        settings.MIGRATION_MODULES = {
            config.label: None for config in apps.get_app_configs()
            if config.label not in {"rebac", "contenttypes"}
        }
        failures = ComposedTestRunner(verbosity=1, interactive=False).run_tests(args.test_label)
        args.output.write_text(json.dumps({"failures": failures, "vendor": connection.vendor}) + "\n")
        raise SystemExit(bool(failures))
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
            + serialized
            + ").items()})\n"
        )
        return
    if args.action == "schemas":
        from angee.graphql.schema import GraphQLSchemas

        result = GraphQLSchemas.from_discovery().render_sdl()
    else:
        result = resource_values() if args.action == "resources" else model_snapshot()
    args.output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")


if __name__ == "__main__":
    main()
