"""Fresh-process host for emitted-model resource and compatibility checks.

Run this file directly so Django constructs the real generated models without
sharing pytest's hand-built source-addon models or global app registry. Only the
temporary host (authored config/history and generated runtime) is written. Native addon tests use Django's test
runner with SQLite by default; ``--test-postgresql`` accepts the runner's
PostgreSQL DATABASE_URL and creates a separate native test database.
"""

from __future__ import annotations

import argparse
import importlib
import json
import os
import subprocess
import sys
from contextlib import nullcontext
from pathlib import Path
from typing import Any
from unittest import TestSuite

import environ

COMPOSED_TEST_TIMEOUT = 1200
"""Stop a composed-host subprocess that hangs; this is a hang bound, not a time budget.

Every caller that starts ``composed_host.py`` passes this one value. The rule: stay
above the slowest real CI run times the run-to-run spread of CI runners, and well
under a CI shard's own 40-minute limit, so a hang ends with the test named instead
of the job ending with no verdict. Measured on 2026-10-14: the slowest is the
workflows posts group on PostgreSQL, 187 s on an idle workstation; on CI it took
467 s in one run and over 600 s in two others, where the bound was then 600 s and
stopped it. A SQLite CI shard runs project setup in up to 269 s. One shard's time
varied up to 1.8 times between runs; over 600 s times 1.8 is over 1,080 s.
"""


def run_composed_tests(
    tmp_path: Path,
    test_label: str,
    *,
    app: str | tuple[str, ...],
    test_postgresql: bool = False,
    addon_dirs: tuple[Path, ...] = (),
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
            *(argument for path in addon_dirs for argument in ("--addon-dir", str(path))),
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

    # Native namespace identity follows one physical source, including symlinked slots.
    source_root = source_root.resolve()
    runtime_dir = runtime_dir.resolve()
    extra_addon_dirs = [path.resolve() for path in extra_addon_dirs]
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
    sys.path[:0] = [str(source_root / "core"), str(source_root), *(str(path) for path in addon_dirs)]
    for name in tuple(os.environ):
        if name.startswith(("ANGEE_", "YAMLCONF_")) or name in {"DJANGO_SETTINGS_MODULE", "DATABASE_URL"}:
            os.environ.pop(name)

    import django
    import yaml
    from django.conf import settings

    from angee.addons import addon_entry_points
    from angee.build import build_runtime
    from angee.project import PROJECT_DIR_ENV

    installed_apps = (
        list(root_apps) if root_apps is not None else [
            name for name in addon_entry_points() if include_examples or name.startswith("angee.")
        ]
    )
    host_root = runtime_dir.parent
    host_root.mkdir(parents=True, exist_ok=True)
    declarations = host_root / "settings.yaml"
    declarations.write_text(yaml.safe_dump({
        "SECRET_KEY": "isolated-composition-check",
        "COMPOSE_RUNTIME_DIR": str(runtime_dir),
        "ANGEE_ADDON_DIRS": [str(path) for path in addon_dirs],
        "INSTALLED_APPS": installed_apps,
        "ANGEE_MONEY_REFERENCE_CURRENCY": "USD",
    }, sort_keys=True), encoding="utf-8")
    # The explicit host worker is the sole compiler. It populates Django in
    # its own interpreter; this interpreter then serves the prebuilt ordinary
    # models, just as the installed host does. No global registry is reset.
    python_paths = [str(source_root / "core"), str(source_root), *(str(path) for path in addon_dirs)]
    if os.environ.get("PYTHONPATH"):
        python_paths.append(os.environ["PYTHONPATH"])
    os.environ["PYTHONPATH"] = os.pathsep.join(python_paths)
    build_runtime(host_root, runtime_dir, check_history=False)
    # This temporary host is the deployment root as well as the authored root;
    # an ancestor checkout's stack is never this test process's deployment input.
    os.environ[PROJECT_DIR_ENV] = str(host_root)
    import runpy

    namespace = {
        name: value for name, value in runpy.run_path(str(runtime_dir / "settings.py")).items()
        if name.isupper()
    }
    # Only the test runner's database differs from the offline build. It owns
    # the native test DB name and never projects credentials into the artifact.
    namespace["DATABASES"] = {"default": database}
    settings.configure(**namespace)
    django.setup()
    if root_apps is None:
        importlib.import_module("tests.statement_models")


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
            history_model = getattr(getattr(model, "history", None), "model", None)
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
                "history": history_model._meta.label_lower if history_model is not None else None,
                "checks": [{"id": issue.id, "message": str(issue)} for issue in model.check()],
            }
    return snapshot


def reset_migration_history_report() -> dict[str, Any]:
    """Exercise a disposable composed history against an unchanged schema."""

    from io import StringIO
    from unittest.mock import patch

    from django.apps import apps
    from django.core.management import call_command
    from django.core.management.base import CommandError
    from django.db import connection
    from django.db.migrations.autodetector import MigrationAutodetector
    from django.db.migrations.executor import MigrationExecutor
    from django.db.migrations.loader import MigrationLoader
    from django.db.migrations.recorder import MigrationRecorder
    from django.db.migrations.state import ProjectState
    from django.utils.module_loading import module_dir

    from angee.apps import AngeeConfig
    from angee.compose.migrations import reset_migration_history
    from angee.compose.runtime import Runtime

    config = apps.get_app_config("angee")
    assert isinstance(config, AngeeConfig)
    labels = tuple(sorted(config.labels))
    runtime = Runtime.from_django()
    call_command("makemigrations", *labels, interactive=False, include_header=False, verbosity=0)
    history_dirs = {
        label: Path(module_dir(importlib.import_module(MigrationLoader.migrations_module(label)[0])))
        for label in labels
    }
    # A fresh initial need not have the same key as the stack's applied one.
    for label in labels:
        initial = history_dirs[label] / "0001_initial.py"
        source = initial.read_text().replace("'0001_initial'", "'0001_previous'")
        initial.rename(initial.with_name("0001_previous.py"))
        initial.with_name("0001_previous.py").write_text(source)

    try:
        call_command("reset_migration_history", skip_checks=True, verbosity=0)
    except CommandError as error:
        pending_error = str(error)
    else:
        raise AssertionError("reset accepted unapplied composed migrations")

    call_command("migrate", interactive=False, skip_checks=True, verbosity=0)
    field = apps.get_model("alpha", "Source")._meta.get_field("name")
    original_length = field.max_length
    field.max_length = original_length + 1
    try:
        try:
            call_command("reset_migration_history", skip_checks=True, verbosity=0)
        except CommandError as error:
            drift_error = str(error)
        else:
            raise AssertionError("reset accepted model drift")
    finally:
        field.max_length = original_length

    call_command(
        "makemigrations",
        labels[0],
        empty=True,
        name="history_probe",
        interactive=False,
        include_header=False,
        verbosity=0,
    )
    call_command("migrate", interactive=False, skip_checks=True, verbosity=0)
    recorder = MigrationRecorder(connection)
    old_rows = sorted(recorder.migration_qs.filter(app__in=labels).values_list("app", "name"))
    noncomposed_before = list(recorder.migration_qs.exclude(app__in=labels).order_by("pk").values())

    def migration_sources() -> dict[Path, bytes]:
        return {
            path: path.read_bytes()
            for label in labels
            for path in history_dirs[label].glob("*.py")
        }

    original_sources = migration_sources()
    original_records = list(recorder.migration_qs.order_by("pk").values())
    failure_rollback = {}
    for phase in ("generation", "recording"):
        original = call_command if phase == "generation" else MigrationRecorder.record_unapplied

        def fail_after_write(*args, **kwargs):
            original(*args, **kwargs)
            raise RuntimeError("injected reset failure")

        target = "angee.compose.migrations.call_command" if phase == "generation" else (
            "django.db.migrations.recorder.MigrationRecorder.record_unapplied"
        )
        with patch(target, autospec=True, side_effect=fail_after_write):
            try:
                reset_migration_history(labels)
            except RuntimeError as error:
                if str(error) != "injected reset failure":
                    raise
            else:
                raise AssertionError(f"reset did not reach the {phase} failure")
        failure_rollback[phase] = (
            migration_sources() == original_sources
            and list(recorder.migration_qs.order_by("pk").values()) == original_records
        )
        MigrationExecutor(connection).loader.check_consistent_history(connection)

    output = StringIO()
    call_command(
        "reset_migration_history",
        skip_checks=True,
        verbosity=0,
        stdout=output,
    )

    new_rows = sorted(recorder.migration_qs.filter(app__in=labels).values_list("app", "name"))
    noncomposed_after = list(recorder.migration_qs.exclude(app__in=labels).order_by("pk").values())
    files = {
        label: sorted(
            path.name
            for path in history_dirs[label].glob("*.py")
            if path.name != "__init__.py"
        )
        for label in labels
    }
    executor = MigrationExecutor(connection)
    executor.loader.check_consistent_history(connection)
    targets = tuple(node for label in labels for node in executor.loader.graph.leaf_nodes(label))
    changes = MigrationAutodetector(
        executor.loader.project_state(),
        ProjectState.from_apps(apps),
    ).changes(
        graph=executor.loader.graph,
        trim_to_apps=set(labels),
        convert_apps=set(labels),
    )
    runtime.check()
    reset_sources = migration_sources()
    reset_records = list(recorder.migration_qs.order_by("pk").values())
    reset_migration_history(labels)
    replay_unchanged = (
        migration_sources() == reset_sources
        and list(recorder.migration_qs.order_by("pk").values()) == reset_records
    )
    alpha = executor.loader.disk_migrations["alpha", "0001_initial"]
    return {
        "labels": labels,
        "pending_error": pending_error,
        "drift_error": drift_error,
        "old_rows": old_rows,
        "new_rows": new_rows,
        "noncomposed_unchanged": noncomposed_before == noncomposed_after,
        "external_dependency_applied": ("external", "0001_initial") in recorder.applied_migrations(),
        "failure_rollback": failure_rollback,
        "replay_unchanged": replay_unchanged,
        "files": files,
        "plan_empty": executor.migration_plan(targets) == [],
        "changes_empty": changes == {},
        "runtime_check": True,
        "declarations": len(runtime.runtime_migrations().plan()),
        "alpha_dependencies": alpha.dependencies,
        "command_output": output.getvalue().splitlines(),
    }


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
        help="Root app to compose (repeatable); dependencies are resolved from installed package metadata.",
    )
    parser.add_argument(
        "--no-examples",
        action="store_true",
        help="Exclude showcase addons from the composed host profile.",
    )
    parser.add_argument(
        "--action",
        choices=("resources", "snapshot", "state", "tests", "schemas", "reset-migrations"),
        default="resources",
    )
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
    from django.conf import settings
    from django.utils.module_loading import import_string

    context_path = getattr(settings, "ANGEE_MANAGEMENT_CONTEXT", "")
    boundary = (
        import_string(context_path)(args.action)
        if context_path and args.action not in {"tests", "reset-migrations"}
        else nullcontext()
    )
    with boundary:
        run(args)


def run(args: argparse.Namespace) -> None:
    """Dispatch one bootstrapped host action without a process-wide test boundary."""

    if args.action == "tests":
        from django.apps import apps
        from django.conf import settings
        from django.core.management import call_command
        from django.db import connection
        from django.test import TransactionTestCase
        from django.test.runner import DiscoverRunner

        class ComposedTestRunner(DiscoverRunner):
            """Prepare the generated host's schema before Django checks its models."""

            def setup_databases(self, **kwargs: Any) -> Any:
                databases = super().setup_databases(**kwargs)
                if "default" in kwargs.get("aliases", ()):
                    call_command("rebac", "sync", verbosity=0)
                return databases

            def build_suite(self, *test_labels: Any, **kwargs: Any) -> TestSuite:
                suite = super().build_suite(*test_labels, **kwargs)
                if not apps.is_installed("angee.provenance"):
                    return suite

                from angee.provenance.context import changeset
                from angee.provenance.testing import load_statement_sources

                def fixture_boundaries(tests: TestSuite) -> None:
                    for test in tests:
                        if isinstance(test, TestSuite):
                            fixture_boundaries(test)
                        else:

                            def set_up(test=test, original=test.setUp) -> None:
                                test.enterContext(
                                    changeset(
                                        fresh=True,
                                        change_reason=f"manage.{args.action}.{test.id()}.fixtures",
                                        process=True,
                                    )
                                )
                                original()
                                # After the test's own setUp: a transactional test re-syncs the REBAC
                                # schema its flush emptied, so the principal bootstrap finds it synced
                                # and only loads the Rule/Seed rows instead of syncing a second time.
                                if isinstance(test, TransactionTestCase):
                                    load_statement_sources("parties")
                                    load_statement_sources("messaging")

                            setattr(test, "setUp", set_up)

                fixture_boundaries(suite)
                return suite

        assert args.test_label, "Native tests require explicit test labels"
        if not args.test_postgresql:
            assert settings.DATABASES["default"]["ENGINE"] == "django.db.backends.sqlite3"
            assert settings.DATABASES["default"]["NAME"] == ":memory:"
        settings.ANGEE_GRAPHQL_ALLOW_INMEMORY_CHANNEL_LAYER = True
        # This disposable host has no migration history. Sync the whole current
        # model graph together, including contenttypes and REBAC's witness table;
        # mixed sync/migrate ordering leaves generated foreign keys dangling.
        settings.MIGRATION_MODULES = {config.label: None for config in apps.get_app_configs()}
        failures = ComposedTestRunner(verbosity=1, interactive=False).run_tests(args.test_label)
        args.output.write_text(json.dumps({"failures": failures, "vendor": connection.vendor}) + "\n")
        raise SystemExit(bool(failures))
    if args.action == "state":
        from django.apps import apps
        from django.core.management import call_command
        from django.db.migrations.state import ProjectState
        from django.db.migrations.writer import MigrationWriter

        call_command("check", verbosity=1)
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
    if args.action == "reset-migrations":
        args.output.write_text(json.dumps(reset_migration_history_report(), indent=2, sort_keys=True) + "\n")
        return
    if args.action == "schemas":
        from angee.graphql.schema import GraphQLSchemas

        result = GraphQLSchemas.from_discovery().render_sdl()
    else:
        result = resource_values() if args.action == "resources" else model_snapshot()
    args.output.write_text(json.dumps(result, indent=2, sort_keys=True) + "\n")


if __name__ == "__main__":
    main()
