"""Angee provision hooks compose native commands in one selected process."""

from __future__ import annotations

import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest
import strawberry
from django.apps import apps
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import override_settings
from django.test.utils import override_system_checks
from rebac import schema_changes
from rebac.backends import reset_backend
from rebac.checks import check_universal_admin_in_roles
from rebac.errors import SchemaError
from rebac.models import SchemaRelation

from angee.base import provision
from angee.base.management.commands.provision import Command
from angee.graphql.checks import check_graphql_schemas
from angee.graphql.schema import GraphQLSchemas, SchemaParts
from angee.graphql.sdl import SchemaIntegration
from tests.composed_host import COMPOSED_TEST_TIMEOUT


def _environment(tmp_path: Path) -> dict[str, str]:
    source_root = Path(__file__).resolve().parents[1]
    environment = dict(os.environ)
    environment.pop("DJANGO_SETTINGS_MODULE", None)
    environment.pop("DJANGO_COMPOSE_MODE", None)
    environment["PYTHONPATH"] = os.pathsep.join(
        filter(None, (str(tmp_path), str(source_root), environment.get("PYTHONPATH", "")))
    )
    return environment


def _write_topology_host(root: Path) -> Path:
    """Write a native host recording Angee's real step and hook dispatch."""

    package = root / "probe"
    package.mkdir()
    (package / "__init__.py").write_text("")
    (root / "manage.py").write_text(
        "import os\nfrom django_compose.management import execute\n"
        "os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'probe.settings')\nexecute()\n",
    )
    (package / "settings.py").write_text(
        "from pathlib import Path\nfrom django_compose.conf import configure\n"
        "SECRET_KEY='provision-process-probe'\nINSTALLED_APPS=['probe.apps.ProbeConfig']\n"
        f"COMPOSE_OUTPUT_DIR=Path({str(root / 'runtime')!r})\nCOMPOSE_OUTPUT_MODULE='runtime'\n"
        f"DATABASES={{'default': {{'ENGINE':'django.db.backends.sqlite3','NAME':{str(root / 'probe.sqlite3')!r}}}}}\n"
        "configure(globals())\n",
    )
    (package / "apps.py").write_text("""import json
import os
from pathlib import Path
from django.apps import AppConfig

def record(step):
    with (Path(__file__).resolve().parent.parent / 'events.jsonl').open('a') as stream:
        stream.write(json.dumps({'step':step,'pid':os.getpid()})+'\\n')

class ProbeConfig(AppConfig):
    name='probe'
    def ready(self):
        from angee.base import provision
        original=provision.run_step
        def run_step(command,step):
            record(step)
            return original(command,step)
        provision.run_step=run_step
    def compose_provision(self,context):
        context.call('probe_step',database=context.database)
""")
    commands = package / "management/commands"
    commands.mkdir(parents=True)
    (commands.parent / "__init__.py").write_text("")
    (commands / "__init__.py").write_text("")
    (commands / "provision.py").write_text("from angee.base.management.commands.provision import Command\n")
    (commands / "probe_step.py").write_text("""from django.core.management.base import BaseCommand
from probe.apps import record
class Command(BaseCommand):
    requires_system_checks=[]
    def add_arguments(self,parser):
        parser.add_argument('--database')
    def handle(self,**options):
        record(['hook',options['database']])
""")
    return root / "manage.py"


def test_provision_runs_native_steps_and_addon_hooks_in_one_process(tmp_path: Path) -> None:
    """Native prebuilt checks/migrate and AppConfig hooks share selected command IO."""

    manage_py = _write_topology_host(tmp_path)
    environment = _environment(tmp_path)
    for arguments in (("compose",), ("provision", "--wait-db", "1")):
        result = subprocess.run(
            [sys.executable, str(manage_py), *arguments], cwd=tmp_path, env=environment,
            capture_output=True, text=True, timeout=60, check=False,
        )
        assert result.returncode == 0, result.stdout + result.stderr
    events = [json.loads(line) for line in (tmp_path / "events.jsonl").read_text().splitlines()]
    assert [event["step"] for event in events] == [
        ["check_migrations"], ["check_migrations", "--imports"],
        ["migrate", "--database", "default", "--noinput", "--skip-checks"],
        ["hook", "default"], ["check", "--database", "default"],
    ]
    assert len({event["pid"] for event in events}) == 1


def test_native_composed_test_runner_does_not_require_provenance(tmp_path: Path) -> None:
    """The source test host can run a plain Django app test without addon boundaries."""

    root = Path(__file__).resolve().parents[1]
    addon = tmp_path / "addons" / "native_probe"
    addon.mkdir(parents=True)
    (addon / "__init__.py").write_text("")
    (addon / "tests.py").write_text(
        'from django.apps import apps\nfrom django.test import SimpleTestCase\n'
        'class CoreTests(SimpleTestCase):\n'
        '    def test_native_host(self):\n'
        '        self.assertTrue(apps.is_installed("django_compose"))\n'
        '        self.assertFalse(apps.is_installed("angee.provenance"))\n'
    )
    report = tmp_path / "report.json"
    result = subprocess.run(
        [sys.executable, str(root / "tests/composed_host.py"), "--runtime-dir", str(tmp_path / "runtime"),
         "--addon-dir", str(addon.parent), "--app", "django.contrib.contenttypes",
         "--app", "django.contrib.auth", "--app", "native_probe",
         "--no-examples", "--action", "tests",
         "--test-label", "native_probe.tests", "--output", str(report)],
        cwd=root, env=_environment(tmp_path), capture_output=True, text=True,
        timeout=COMPOSED_TEST_TIMEOUT, check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert json.loads(report.read_text()) == {"failures": 0, "vendor": "sqlite"}


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
@schema_changes()
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
        override_settings(REBAC_UNIVERSAL_ADMIN_ROLE="angee/role:admin", ANGEE_REBAC_FORCE_OVERWRITE=True),
        override_system_checks([check_universal_admin_in_roles]),
    ):
        with pytest.raises(SchemaError, match="names a permission"):
            call_command("check", verbosity=0)
        command = Command()
        apps.get_app_config("base").compose_provision(
            provision.ProvisionContext("default", command.stdout, command.stderr),
        )
        provision.run_step(command, ["check"])


@pytest.mark.django_db
def test_graphql_check_then_schema_command_reuses_the_built_schema(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The native check and prebuilt SDL command share discovery's completed schema build."""

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
        with override_settings(COMPOSE_OUTPUT_DIR=tmp_path / "runtime"):
            for relative, content in SchemaIntegration().render(None, None).items():
                target = tmp_path / "runtime" / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(content)
            call_command("check", verbosity=0)
            call_command("schema", check=True, verbosity=0)
            with pytest.raises(CommandError, match="compiler-owned"):
                call_command("schema", verbosity=0)

    assert len(builds) == 1
    assert schemas.build("public") is builds[0]
    assert "value: String!" in (tmp_path / "runtime" / "schemas" / "public.graphql").read_text()
