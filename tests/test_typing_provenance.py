"""Optional provenance typing composes through native mypy plugin registration."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from tests.composed_host import COMPOSED_TEST_TIMEOUT


def test_provenance_plugin_types_concrete_storage_without_angee_host(tmp_path: Path) -> None:
    """The plugin needs only native Django and the generic composition plugin."""

    root = Path(__file__).resolve().parents[1]
    app = tmp_path / "storage_probe"
    app.mkdir()
    (app / "__init__.py").write_text("")
    (app / "models.py").write_text(
        "from django.db import models\n"
        "class Changeset(models.Model):\n"
        "    runtime = True\n"
        "    note = models.CharField(max_length=40)\n"
        "    class Meta:\n"
        "        abstract = True\n"
        "class OtherChangeset(models.Model):\n"
        "    runtime = True\n"
        "    note = models.IntegerField()\n"
        "    class Meta:\n"
        "        abstract = True\n"
    )
    (tmp_path / "storage_settings.py").write_text(
        "import os\nfrom pathlib import Path\n"
        "SECRET_KEY = 'optional-provenance-typing'\n"
        "INSTALLED_APPS = ['django.contrib.contenttypes', 'storage_probe', 'angee.compose']\n"
        "DATABASES = {'default': {'ENGINE': 'django.db.backends.sqlite3', 'NAME': ':memory:'}}\n"
        "DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'\n"
        f"COMPOSE_RUNTIME_DIR = Path({str(tmp_path / 'storage_runtime')!r})\n"
        "COMPOSE_RUNTIME_MODULE = 'storage_runtime'\n"
        "ANGEE_CHANGESET_MODEL = os.environ.get('PROVENANCE_TYPING_STORAGE', 'storage_probe.Changeset')\n"
    )
    config = tmp_path / "mypy.ini"
    config.write_text(
        "[mypy]\nplugins = angee.compose.typing, angee.provenance.typing\n"
        f"mypy_path = {tmp_path},{root},{root / 'addons'}\n"
        f"cache_dir = {tmp_path / 'cache'}\nignore_missing_imports = True\n"
        "[mypy.plugins.django-stubs]\ndjango_settings_module = storage_settings\n"
    )
    valid = tmp_path / "valid.py"
    valid.write_text(
        "from typing import assert_type\n"
        "from angee.provenance.context import changeset_model, current_changeset, require_changeset\n"
        "from storage_runtime.storage_probe.models import Changeset\n"
        "assert_type(changeset_model(), type[Changeset])\n"
        "assert_type(current_changeset(), Changeset | None)\n"
        "assert_type(require_changeset(), Changeset)\n"
        "assert_type(require_changeset().note, str)\n"
        "assert_type(changeset_model().objects.get().note, str)\n"
    )
    invalid = tmp_path / "invalid.py"
    invalid.write_text(
        "from angee.provenance.context import changeset_model, require_changeset\n"
        "wrong: int = require_changeset().note\n"
        "changeset_model().objects.missing_method()\n"
    )
    env = dict(os.environ, DJANGO_SETTINGS_MODULE="storage_settings")
    env["PYTHONPATH"] = os.pathsep.join(filter(None, (
        str(tmp_path), str(root), str(root / "addons"), env.get("PYTHONPATH"),
    )))
    command = [sys.executable, "-m", "mypy", "--config-file", str(config)]
    positive = subprocess.run(
        [*command, str(valid)], cwd=root, env=env, capture_output=True, text=True, timeout=COMPOSED_TEST_TIMEOUT,
    )
    assert positive.returncode == 0, positive.stdout + positive.stderr
    negative = subprocess.run(
        [*command, str(invalid)], cwd=root, env=env, capture_output=True, text=True, timeout=COMPOSED_TEST_TIMEOUT,
    )
    assert negative.returncode == 1, negative.stdout + negative.stderr
    assert negative.stdout.count(": error:") == 2, negative.stdout

    # Source files and config stay unchanged: the native cache hook must notice
    # that this host selected another concrete storage model through its input.
    switched = subprocess.run(
        [*command, str(valid)], cwd=root,
        env={**env, "PROVENANCE_TYPING_STORAGE": "storage_probe.OtherChangeset"},
        capture_output=True, text=True, timeout=COMPOSED_TEST_TIMEOUT,
    )
    assert switched.returncode == 1, switched.stdout + switched.stderr
    assert "OtherChangeset" in switched.stdout, switched.stdout


def test_provenance_plugin_keeps_protocol_without_an_installed_storage_app(tmp_path: Path) -> None:
    """A shared typing config does not activate the optional persistence app."""

    root = Path(__file__).resolve().parents[1]
    (tmp_path / "absent_storage_settings.py").write_text(
        "SECRET_KEY = 'optional-storage-absent'\nINSTALLED_APPS = []\n"
        "DATABASES = {'default': {'ENGINE': 'django.db.backends.sqlite3', 'NAME': ':memory:'}}\n",
    )
    config = tmp_path / "mypy.ini"
    config.write_text(
        "[mypy]\nplugins = angee.compose.typing, angee.provenance.typing\n"
        f"mypy_path = {tmp_path},{root},{root / 'addons'}\n"
        f"cache_dir = {tmp_path / 'cache'}\nignore_missing_imports = True\n"
        "[mypy.plugins.django-stubs]\ndjango_settings_module = absent_storage_settings\n",
    )
    source = tmp_path / "consumer.py"
    source.write_text(
        "from typing import assert_type\nfrom django.db.models import Model\n"
        "from angee.provenance.context import ChangesetLike, changeset_model, current_changeset, require_changeset\n"
        "assert_type(changeset_model(), type[Model])\n"
        "assert_type(current_changeset(), ChangesetLike | None)\n"
        "assert_type(require_changeset(), ChangesetLike)\n",
    )
    env = dict(os.environ, DJANGO_SETTINGS_MODULE="absent_storage_settings")
    env["PYTHONPATH"] = os.pathsep.join(filter(None, (
        str(tmp_path), str(root), str(root / "addons"), env.get("PYTHONPATH"),
    )))
    result = subprocess.run(
        [sys.executable, "-m", "mypy", "--config-file", str(config), str(source)],
        cwd=root, env=env, capture_output=True, text=True, timeout=COMPOSED_TEST_TIMEOUT,
    )
    assert result.returncode == 0, result.stdout + result.stderr
