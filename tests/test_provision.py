"""Integration coverage for provision's process and native-owner boundaries."""

from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest
import strawberry
from django.core.management import call_command
from django.test import override_settings
from django.test.utils import override_system_checks
from rebac.backends import reset_backend
from rebac.checks import check_universal_admin_in_roles
from rebac.errors import SchemaError
from rebac.models import SchemaRelation

from angee.compose.management.commands.angee import Command
from angee.graphql.checks import check_graphql_schemas
from angee.graphql.schema import GraphQLSchemas, SchemaParts


def _environment(tmp_path: Path) -> dict[str, str]:
    source_root = Path(__file__).resolve().parents[1]
    environment = dict(os.environ)
    environment.pop("DJANGO_SETTINGS_MODULE", None)
    environment["PYTHONPATH"] = os.pathsep.join(
        filter(None, (str(tmp_path), str(source_root), environment.get("PYTHONPATH", "")))
    )
    return environment


def _write_topology_host(root: Path) -> Path:
    """Write a Django host that records only provision orchestration facts."""

    package = root / "probe"
    package.mkdir()
    (package / "__init__.py").write_text("", encoding="utf-8")
    (root / "manage.py").write_text(
        """import os
import sys
from django.core.management import execute_from_command_line
os.environ.setdefault("DJANGO_SETTINGS_MODULE", "probe.settings")
execute_from_command_line(sys.argv)
""",
        encoding="utf-8",
    )
    (package / "settings.py").write_text(
        f"""from django.apps import AppConfig
class BareComposeConfig(AppConfig):
    name = "angee.compose"
    label = "compose"
SECRET_KEY = "provision-process-probe"
INSTALLED_APPS = ["django.contrib.contenttypes", "probe.apps.ProbeConfig", "probe.settings.BareComposeConfig"]
DATABASES = {{"default": {{"ENGINE": "django.db.backends.sqlite3", "NAME": {str(root / "probe.sqlite3")!r}}}}}
""",
        encoding="utf-8",
    )
    (package / "apps.py").write_text(
        """from django.apps import AppConfig
class ProbeConfig(AppConfig):
    name = "probe"
    def ready(self):
        import json
        import os
        from pathlib import Path
        import angee.compose.management.commands.angee as provision
        events = Path(__file__).resolve().parent.parent / "events.jsonl"
        def record_step(command, step):
            del command
            with events.open("a", encoding="utf-8") as stream:
                stream.write(json.dumps({"step": step, "pid": os.getpid()}) + "\\n")
        provision.Command._run_step = record_step
""",
        encoding="utf-8",
    )
    return root / "manage.py"


def test_provision_builds_then_runs_one_fresh_post_build_process(tmp_path: Path) -> None:
    """The parent runs build and one fresh child owns the remaining ordered commands."""

    manage_py = _write_topology_host(tmp_path)
    result = subprocess.run(
        [sys.executable, str(manage_py), "angee", "provision", "--wait-db", "1", "--demo", "--force-rebac"],
        cwd=tmp_path,
        env=_environment(tmp_path),
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert result.returncode == 0, f"provision failed:\n{result.stdout}\n{result.stderr}"
    events = [json.loads(line) for line in (tmp_path / "events.jsonl").read_text().splitlines()]
    assert [event["step"] for event in events] == [
        ["angee", "build"],
        ["makemigrations", "--noinput", "--skip-checks"],
        ["migrate", "--noinput", "--skip-checks"],
        ["reconcile_permissions"],
        ["rebac", "--skip-checks", "sync", "--yes", "--force-overwrite"],
        ["check"],
        ["resources", "load", "--include-demo"],
        ["schema"],
    ]
    assert events[0]["pid"] not in {event["pid"] for event in events[1:]}
    assert len({event["pid"] for event in events[1:]}) == 1


def test_native_makemigrations_then_migrate_sees_new_file_in_same_process(tmp_path: Path) -> None:
    """Django reloads a migration file created earlier in the same process."""

    app = tmp_path / "native_app"
    app.mkdir()
    (app / "__init__.py").write_text("", encoding="utf-8")
    (app / "models.py").write_text(
        "from django.db import models\n\nclass Entry(models.Model):\n    value = models.CharField(max_length=20)\n",
        encoding="utf-8",
    )
    database = tmp_path / "native.sqlite3"
    script = tmp_path / "migrate_in_process.py"
    script.write_text(
        f"""from django.conf import settings
settings.configure(
    SECRET_KEY="probe",
    INSTALLED_APPS=["django.contrib.contenttypes", "native_app"],
    DATABASES={{"default": {{"ENGINE": "django.db.backends.sqlite3", "NAME": {str(database)!r}}}}},
    DEFAULT_AUTO_FIELD="django.db.models.BigAutoField",
)
import django
django.setup()
from django.core.management import call_command
call_command("makemigrations", "native_app", verbosity=0, skip_checks=True)
call_command("migrate", verbosity=0, interactive=False, skip_checks=True)
""",
        encoding="utf-8",
    )
    result = subprocess.run(
        [sys.executable, str(script)],
        cwd=tmp_path,
        env=_environment(tmp_path),
        capture_output=True,
        text=True,
        timeout=60,
        check=False,
    )
    assert result.returncode == 0, f"native migration sequence failed:\n{result.stdout}\n{result.stderr}"
    assert (app / "migrations" / "0001_initial.py").is_file()
    with sqlite3.connect(database) as connection:
        assert connection.execute(
            "select name from sqlite_master where type = 'table' and name = 'native_app_entry'"
        ).fetchone() == ("native_app_entry",)


@pytest.mark.django_db
def test_rebac_sync_invalidates_the_native_backend_cache() -> None:
    """The native sync command discards the process-cached REBAC backend."""

    from rebac.backends import backend

    before = backend()
    call_command("rebac", "sync", "--yes", verbosity=0)
    assert backend() is not before


@pytest.mark.django_db
def test_provision_sync_replaces_invalid_historical_subject_sets_before_checks() -> None:
    """Old permission usersets cannot prevent sync from installing their replacement."""

    call_command("rebac", "sync", "--yes", verbosity=0)
    relation = SchemaRelation.objects.get(definition__resource_type="storage/role", name="includes")
    relation.allowed_subjects = [{"type": "storage/role", "relation": "effective_member", "wildcard": False}]
    relation.save(update_fields=["allowed_subjects"])
    reset_backend()

    # Exercise a native schema-reading check without requiring a fully composed
    # concrete model graph in this bare test host.
    with (
        override_settings(REBAC_UNIVERSAL_ADMIN_ROLE="angee/role:admin"),
        override_system_checks([check_universal_admin_in_roles]),
    ):
        with pytest.raises(SchemaError, match="names a permission"):
            call_command("check", verbosity=0)
        command = Command()
        plan = command._provision_plan({"force_rebac": True, "demo": False, "bootstrap_admin": False})
        command._run_step(next(step for step in plan if step[0] == "rebac"))
        command._run_step(["check"])


@pytest.mark.django_db
def test_graphql_check_then_schema_command_reuses_the_built_schema(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The native check and SDL command share discovery's completed schema build."""

    @strawberry.type
    class Query:
        value: str = "ready"

    schemas = GraphQLSchemas([])
    schemas.__dict__["parts"] = {"public": SchemaParts(query=(Query,))}
    original_build = schemas._build
    builds: list[strawberry.Schema] = []

    def counted_build(name: str) -> strawberry.Schema:
        built = original_build(name)
        builds.append(built)
        return built

    monkeypatch.setattr(schemas, "_build", counted_build)
    monkeypatch.setattr(GraphQLSchemas, "from_discovery", classmethod(lambda cls: schemas))
    with override_system_checks([check_graphql_schemas]):
        with override_settings(ANGEE_RUNTIME_DIR=tmp_path / "runtime"):
            call_command("check", verbosity=0)
            call_command("schema", verbosity=0)

    assert len(builds) == 1
    assert schemas.build("public") is builds[0]
    assert "value: String!" in (tmp_path / "runtime" / "schemas" / "public.graphql").read_text()
