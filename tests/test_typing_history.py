"""Native history declarations remain static typing artifacts, never models."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


def test_native_history_projection_preserves_runtime_and_type_checks(tmp_path: Path) -> None:
    root = Path(__file__).resolve().parents[1]
    runtime = tmp_path / "history_runtime"
    settings = tmp_path / "history_settings.py"
    settings.write_text(
        "from pathlib import Path\n"
        "SECRET_KEY = 'native-history-typing'\n"
        "INSTALLED_APPS = ['django.contrib.contenttypes', 'django.contrib.auth', 'rebac', 'angee', "
        "'tests.typing_history.apps.TypingHistoryConfig', 'angee.compose', 'tests.typing_host.TypingHostConfig']\n"
        "DATABASES = {'default': {'ENGINE': 'django.db.backends.sqlite3', 'NAME': ':memory:'}}\n"
        "DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'\n"
        f"COMPOSE_RUNTIME_DIR = Path({str(runtime)!r})\n"
        "COMPOSE_RUNTIME_MODULE = 'history_runtime'\n"
        "ANGEE_CHANGESET_MODEL = 'typing_history.Changeset'\n"
    )
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join(filter(None, (
        str(tmp_path), str(root), str(root / "addons"), env.get("PYTHONPATH"),
    )))
    env["DJANGO_SETTINGS_MODULE"] = "history_settings"
    native = subprocess.run(
        [sys.executable, "-c", "\n".join([
            "import ast",
            "import django",
            "django.setup()",
            "from django.apps import apps",
            "from django.conf import settings",
            "from django.db.migrations.state import ProjectState",
            "from django.test.utils import override_settings",
            "from pathlib import Path",
            "from tests.typing_host import project_history_models",
            "config = apps.get_app_config('typing_history')",
            "models = dict((model.__name__, model) for model in config.get_models())",
            "assert config.before_projection == ProjectState.from_apps(apps)",
            "assert project_history_models() is False",
            "assert models == dict((model.__name__, model) for model in config.get_models())",
            "source = (settings.COMPOSE_RUNTIME_DIR / 'typing_history/models.py').read_text()",
            "projection = ast.parse(source[source.index('from typing import TYPE_CHECKING'):])",
            "assert len(projection.body) == 2",
            "assert isinstance(projection.body[0], ast.ImportFrom) and projection.body[0].module == 'typing'",
            "assert isinstance(projection.body[1], ast.If)",
            "assert isinstance(projection.body[1].test, ast.Name) and projection.body[1].test.id == 'TYPE_CHECKING'",
            "assert {node.name for node in projection.body[1].body if isinstance(node, ast.ClassDef)} "
            "== {'HistoricalSubject', 'HistoricalSubjectStatement', 'SubjectStatement'}",
            "foreign = settings.COMPOSE_RUNTIME_DIR.parent / 'foreign'",
            "foreign.mkdir()",
            "(foreign / 'unrelated.txt').write_text('preserve')",
            "try:",
            "    with override_settings(COMPOSE_RUNTIME_DIR=foreign):",
            "        project_history_models()",
            "except RuntimeError as error:",
            "    assert 'not an Angee runtime directory' in str(error)",
            "else:",
            "    raise AssertionError('Projection accepted a foreign output tree')",
            "assert list(foreign.iterdir()) == [foreign / 'unrelated.txt']",
        ])],
        cwd=root, env=env, capture_output=True, text=True, check=False,
    )
    assert native.returncode == 0, native.stdout + native.stderr

    config = tmp_path / "mypy.ini"
    config.write_text(
        "[mypy]\nplugins = angee.compose.typing\n"
        f"mypy_path = {tmp_path},{root},{root / 'addons'}\n"
        "ignore_missing_imports = True\ncache_dir = /dev/null\n"
        "[mypy.plugins.django-stubs]\ndjango_settings_module = history_settings\n"
    )
    valid = tmp_path / "valid.py"
    valid.write_text(
        "from typing import assert_type\n"
        "from angee.provenance.statements import Statement\n"
        "from history_runtime.typing_history.models import "
        "Subject, SubjectStatement, HistoricalSubject, HistoricalSubjectStatement\n"
        "assert_type(SubjectStatement.objects.get().title, str | None)\n"
        "assert_type(SubjectStatement.objects.get().rank, Statement.StatementRank)\n"
        "assert_type(SubjectStatement.objects.get().count, int | None)\n"
        "assert_type(SubjectStatement.objects.get().subject, Subject)\n"
        "assert_type(Subject().subjectstatement_set.get().count, int | None)\n"
        "assert_type(HistoricalSubject.objects.get().count, int)\n"
        "assert_type(HistoricalSubjectStatement.objects.get().subject, Subject | None)\n"
    )
    invalid = tmp_path / "invalid.py"
    invalid.write_text(
        "from history_runtime.typing_history.models import Subject, SubjectStatement, Changeset\n"
        "SubjectStatement.objects.get().count = {}\n"
        "SubjectStatement.objects.get().subject = Changeset()\n"
        "Subject().subjectstatement_set.missing_method()\n"
    )
    command = [sys.executable, "-m", "mypy", "--config-file", str(config)]
    positive = subprocess.run(
        [*command, str(valid), str(runtime / 'typing_history' / 'models.py')],
        cwd=root, env=env, capture_output=True, text=True, check=False,
    )
    assert positive.returncode == 0, positive.stdout + positive.stderr
    negative = subprocess.run([*command, str(invalid)], cwd=root, env=env, capture_output=True, text=True, check=False)
    assert negative.returncode == 1, negative.stdout + negative.stderr
    assert negative.stdout.count(": error:") == 3, negative.stdout
    for line in (2, 3, 4):
        assert f"invalid.py:{line}: error:" in negative.stdout, negative.stdout
