"""Project-owned Django history survives composition and runs ordinary data operations."""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import django_compose
import pytest


@pytest.mark.parametrize("selected_apps", [False, True])
def test_composed_apps_use_native_initials_and_project_data_migrations(tmp_path: Path, selected_apps: bool) -> None:
    for name in ("bootstrap_app", "other_app"):
        package = tmp_path / name
        package.mkdir()
        (package / "__init__.py").write_text("")
        (package / "models.py").write_text('''from django.db import models
from django_compose.models import Composable
class Entry(Composable, concrete=True):
    value = models.CharField(max_length=40)
    class Meta:
        abstract = True
''')
    (tmp_path / "bootstrap_settings.py").write_text(f'''from pathlib import Path
from django_compose.conf import configure
SECRET_KEY = "native-history"
INSTALLED_APPS = ["bootstrap_app", "other_app"]
COMPOSE_OUTPUT_MODULE = "runtime"
COMPOSE_OUTPUT_DIR = Path({str(tmp_path / "runtime")!r})
DATABASES = {{"default": {{"ENGINE": "django.db.backends.sqlite3", "NAME": {str(tmp_path / "db.sqlite3")!r}}}}}
DEFAULT_AUTO_FIELD = "django.db.models.BigAutoField"
configure(globals())
''')
    environment = {
        name: value for name, value in os.environ.items()
        if not name.startswith(("ANGEE_", "YAMLCONF_"))
        and name not in {"DATABASE_URL", "DJANGO_SETTINGS_MODULE", "PYTHONPATH", "DJANGO_COMPOSE_MODE"}
    }
    environment.update(
        DJANGO_SETTINGS_MODULE="bootstrap_settings",
        PYTHONPATH=os.pathsep.join((str(tmp_path), str(Path(django_compose.__file__).resolve().parent.parent))),
    )
    compose = "from django_compose.management import execute; execute(['manage.py', 'compose'])"
    built = subprocess.run(
        [sys.executable, "-c", compose], cwd=tmp_path, env=environment,
        capture_output=True, text=True, timeout=60, check=False,
    )
    assert built.returncode == 0, built.stdout + built.stderr
    result = subprocess.run(
        [sys.executable, "-c", r'''
from pathlib import Path
import sys
import django
django.setup()
from django.core.management import call_command
from django.db import connection
from django.db.migrations.recorder import MigrationRecorder

first = Path("runtime/bootstrap_app/migrations")
second = Path("runtime/other_app/migrations")
if sys.argv[1] == "selected":
    call_command("makemigrations", "bootstrap_app", interactive=False, verbosity=0)
    frozen = {p: p.read_bytes() for p in first.glob("*.py")}
    assert list(second.glob("*.py")) == [second / "__init__.py"]
    call_command("makemigrations", "other_app", interactive=False, verbosity=0)
    assert {p: p.read_bytes() for p in first.glob("*.py")} == frozen
else:
    call_command("makemigrations", interactive=False, verbosity=0)
assert (first / "0001_initial.py").is_file()
assert (second / "0001_initial.py").is_file()
assert MigrationRecorder(connection).applied_migrations() == {}
call_command("makemigrations", "bootstrap_app", empty=True, name="seed_entry", verbosity=0)
(first / "0002_seed_entry.py").write_text("""from django.db import migrations
def forwards(apps, schema_editor):
    apps.get_model('bootstrap_app', 'Entry')._base_manager.using(schema_editor.connection.alias).create(value='seeded')
class Migration(migrations.Migration):
    dependencies = [('bootstrap_app', '0001_initial')]
    operations = [migrations.RunPython(forwards, migrations.RunPython.noop)]
""")
''', "selected" if selected_apps else "all"],
        cwd=tmp_path, env=environment, capture_output=True, text=True, timeout=60, check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    history = {path: path.read_bytes() for path in (tmp_path / "runtime").glob("*/migrations/*.py")}
    rebuilt = subprocess.run(
        [sys.executable, "-c", compose], cwd=tmp_path, env=environment,
        capture_output=True, text=True, timeout=60, check=False,
    )
    assert rebuilt.returncode == 0, rebuilt.stdout + rebuilt.stderr
    assert {path: path.read_bytes() for path in (tmp_path / "runtime").glob("*/migrations/*.py")} == history
    applied = subprocess.run(
        [sys.executable, "-c", '''import django
django.setup()
from django.apps import apps
from django.core.management import call_command
call_command('makemigrations', check_changes=True, dry_run=True, interactive=False, verbosity=0)
call_command('migrate', interactive=False, verbosity=0)
assert list(apps.get_model('bootstrap_app', 'Entry')._base_manager.values_list('value', flat=True)) == ['seeded']
'''], cwd=tmp_path, env=environment, capture_output=True, text=True, timeout=60, check=False,
    )
    assert applied.returncode == 0, applied.stdout + applied.stderr
