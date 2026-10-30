"""Preserve actual initial-history regressions against the native compiler owner.

These tests are retained here until the standalone owner adopts their behavior;
they must not be removed based on generic compiler coverage counts.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import django_compose
import pytest


def _write_module(path: Path, text: str = "") -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


@pytest.fixture
def fresh_authoring_host(tmp_path: Path):
    """Build two independent apps with guarded data transitions and no native history."""
    compiler_source = Path(django_compose.__file__).resolve().parent.parent
    package = tmp_path / "bootstrap_app"
    _write_module(package / "__init__.py")
    _write_module(package / "models.py", """from django.db import models
from django_compose.models import Composable
class Entry(Composable, concrete=True):
    value = models.CharField(max_length=40)
    class Meta:
        abstract = True
""")
    _write_module(package / "compose_migrations" / "bootstrap_app" / "__init__.py")
    _write_module(
        package / "compose_migrations" / "bootstrap_app" / "record_bootstrap.py", """from django.db import migrations
def applies(state):
    return ("bootstrap_app", "entry") in state.models
def forwards(apps, schema_editor):
    apps.get_model("bootstrap_app", "Entry")._base_manager.using(schema_editor.connection.alias).create(
        value="transition ran",
    )
class Migration(migrations.Migration):
    dependencies = []
    operations = [migrations.RunPython(forwards, migrations.RunPython.noop)]
""")
    for source in sorted(package.rglob("*")):
        if source.is_file():
            _write_module(
                tmp_path / "other_app" / str(source.relative_to(package)).replace("bootstrap_app", "other_app"),
                source.read_text().replace("bootstrap_app", "other_app").replace("bootstrap-app", "other-app"),
            )
    _write_module(tmp_path / "bootstrap_settings.py", f"""from pathlib import Path
from django_compose.conf import configure
SECRET_KEY = "fresh-authoring"
INSTALLED_APPS = ["bootstrap_app", "other_app"]
COMPOSE_OUTPUT_MODULE = "runtime"
COMPOSE_OUTPUT_DIR = Path({str(tmp_path / "runtime")!r})
DATABASES = {{"default": {{"ENGINE": "django.db.backends.sqlite3", "NAME": {str(tmp_path / "fresh.sqlite3")!r}}}}}
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
configure(globals())
""")
    environment = {
        name: value for name, value in os.environ.items()
        if not name.startswith(("ANGEE_", "YAMLCONF_"))
        and name not in {"DATABASE_URL", "DJANGO_SETTINGS_MODULE", "PYTHONPATH", "COMPOSE_BUILD", "DJANGO_COMPOSE_MODE"}
    }
    environment.update(
        DJANGO_SETTINGS_MODULE="bootstrap_settings", PYTHONPATH=os.pathsep.join((str(tmp_path), str(compiler_source))),
    )
    built = subprocess.run(
        [sys.executable, "-c", "from django_compose.management import execute; execute(['manage.py', 'compose'])"],
        cwd=tmp_path, env=environment,
        capture_output=True, text=True, timeout=60, check=False,
    )
    assert built.returncode == 0, built.stdout + built.stderr
    return tmp_path, environment


@pytest.mark.parametrize("selected_apps", [False, True])
def test_fresh_host_authors_native_initials_before_guarded_transitions(
    fresh_authoring_host, selected_apps: bool,
) -> None:
    """Public authoring handles all apps together or independent selected apps in succession."""
    tmp_path, environment = fresh_authoring_host
    result = subprocess.run(
        [sys.executable, "-c", r"""
from pathlib import Path
import sys
import django
django.setup()
from django.apps import apps
from django.core.management import call_command
from django.core.management.base import CommandError
from django.db import connection
from django.db.migrations.loader import MigrationLoader
from django.db.migrations.recorder import MigrationRecorder

directory = Path("runtime/bootstrap_app/migrations")
assert list(directory.glob("*.py")) == [directory / "__init__.py"]
for flag in ("--check", "--dry-run", "--merge", "--empty", "--update"):
    try:
        call_command("makemigrations", "bootstrap_app", flag, interactive=False, verbosity=0)
    except CommandError as error:
        assert flag not in ("--check", "--dry-run")
        assert "not applicable" in str(error), str(error)
    except SystemExit as error:
        assert flag == "--check" and error.code == 1
    else:
        assert flag == "--dry-run", f"{flag} silently accepted an uninitialized graph"
    assert list(directory.glob("*.py")) == [directory / "__init__.py"]
if sys.argv[1] == "selected":
    call_command("makemigrations", "bootstrap_app", interactive=False, verbosity=0)
    first_sources = {path: path.read_bytes() for path in directory.glob("*.py")}
    second_directory = Path("runtime/other_app/migrations")
    assert list(second_directory.glob("*.py")) == [second_directory / "__init__.py"]
    call_command("makemigrations", "other_app", interactive=False, verbosity=0)
    assert {path: path.read_bytes() for path in directory.glob("*.py")} == first_sources
else:
    call_command("makemigrations", interactive=False, verbosity=0)
loader = MigrationLoader(None, ignore_no_migrations=True)
transition = loader.disk_migrations["bootstrap_app", "record_bootstrap"]
assert transition.dependencies == [("bootstrap_app", "0001_initial")]
assert transition.compose_origin == "bootstrap_app:record_bootstrap"
if sys.argv[1] == "selected":
    assert loader.disk_migrations["other_app", "record_bootstrap"].dependencies == [("other_app", "0001_initial")]
assert MigrationRecorder(connection).applied_migrations() == {}
frozen = {path: path.read_bytes() for path in directory.glob("*.py")}
call_command("makemigrations", interactive=False, verbosity=0)
call_command("makemigrations", check_changes=True, dry_run=True, interactive=False, verbosity=0)
assert {path: path.read_bytes() for path in directory.glob("*.py")} == frozen
call_command("migrate", interactive=False, verbosity=0)
rows = apps.get_model("bootstrap_app", "Entry")._base_manager
assert list(rows.values_list("value", flat=True)) == ["transition ran"]
if sys.argv[1] == "selected":
    rows = apps.get_model("other_app", "Entry")._base_manager
    assert list(rows.values_list("value", flat=True)) == ["transition ran"]
""", "selected" if selected_apps else "all"],
        cwd=tmp_path, env=environment,
        capture_output=True, text=True, timeout=60, check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_fresh_app_initial_refuses_native_dependency_expansion_into_existing_history(fresh_authoring_host) -> None:
    """A new app's FK cannot silently move an existing app's changes ahead of its transitions."""
    tmp_path, environment = fresh_authoring_host
    first = subprocess.run(
        [sys.executable, "-c", "import django; django.setup(); "
         "from django.core.management import call_command; "
         "call_command('makemigrations', 'bootstrap_app', interactive=False, verbosity=0)"],
        cwd=tmp_path, env=environment, capture_output=True, text=True, timeout=60, check=False,
    )
    assert first.returncode == 0, first.stdout + first.stderr
    source = tmp_path / "bootstrap_app/models.py"
    source.write_text(source.read_text() + "\nclass Added(Composable, concrete=True):\n"
                      "    value = models.CharField(max_length=40)\n    class Meta:\n        abstract = True\n")
    other = tmp_path / "other_app/models.py"
    other.write_text(other.read_text().replace(
        "    class Meta:",
        '    added = models.ForeignKey("bootstrap_app.Added", null=True, on_delete=models.CASCADE)\n    class Meta:',
    ))
    rebuilt = subprocess.run(
        [sys.executable, "-c", "from django_compose.management import execute; execute(['manage.py', 'compose'])"],
        cwd=tmp_path, env=environment,
        capture_output=True, text=True, timeout=60, check=False,
    )
    assert rebuilt.returncode == 0, rebuilt.stdout + rebuilt.stderr
    histories = tuple((tmp_path / "runtime").glob("*/migrations/*.py"))
    before = {path: path.read_bytes() for path in histories}
    refused = subprocess.run(
        [sys.executable, "-c", "import django; django.setup(); "
         "from django.core.management import call_command; "
         "call_command('makemigrations', 'other_app', interactive=False, verbosity=0)"],
        cwd=tmp_path, env=environment, capture_output=True, text=True, timeout=60, check=False,
    )
    assert refused.returncode != 0
    assert "author their transitions first: bootstrap_app" in refused.stderr
    assert {path: path.read_bytes() for path in (tmp_path / "runtime").glob("*/migrations/*.py")} == before
