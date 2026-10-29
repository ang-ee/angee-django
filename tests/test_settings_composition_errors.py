"""Authored settings preserve native discovery failures before Django setup."""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from angee.base.testing.packages import write_addon_distribution


def _project(tmp_path: Path) -> None:
    addon = tmp_path / "missing_dependency"
    addon.mkdir(parents=True)
    (addon / "__init__.py").write_text("")
    write_addon_distribution(addon, "missing_dependency")
    metadata = tmp_path / "missing_dependency-0.0.0.dist-info" / "METADATA"
    metadata.write_text(metadata.read_text() + "Requires-Dist: unavailable-dependency\n")
    (tmp_path / "settings.py").write_text(
        "from pathlib import Path\nfrom angee.base.conf import configure\n"
        "COMPOSE_OUTPUT_DIR = Path(__file__).parent / 'runtime'\nconfigure(globals())\n",
    )
    (tmp_path / "settings.yaml").write_text(
        "SECRET_KEY: isolated-composition-test\n"
        "INSTALLED_APPS: [django.contrib.contenttypes, missing_dependency]\n",
    )


def _environment(tmp_path: Path) -> dict[str, str]:
    environment = {name: value for name, value in os.environ.items()
                   if not name.startswith(("ANGEE_", "YAMLCONF_", "DJANGO_COMPOSE_"))}
    environment.update({
        "ANGEE_PROJECT_DIR": str(tmp_path), "DJANGO_SETTINGS_MODULE": "settings",
        "PYTHONPATH": os.pathsep.join(filter(None, (
            str(tmp_path), str(Path(__file__).resolve().parents[1] / "addons"),
            *(str(Path(path).resolve()) for path in os.environ.get("PYTHONPATH", "").split(os.pathsep) if path),
        ))),
    })
    return environment


def test_explicit_build_reports_the_unknown_dependency(tmp_path: Path) -> None:
    _project(tmp_path)
    result = subprocess.run(
        [sys.executable, "-c", "from django_compose.management import execute; execute(['manage.py','compose'])"],
        cwd=tmp_path, env=_environment(tmp_path), capture_output=True, text=True, check=False,
    )
    assert result.returncode != 0
    assert "requires missing installed distribution 'unavailable-dependency'" in result.stderr
    assert "django.contrib.auth" not in result.stderr
    assert "doesn't declare an explicit app_label" not in result.stderr


def test_failed_composition_preserves_unpublished_django_settings(tmp_path: Path) -> None:
    """A failed authored module import leaves native LazySettings unpublished."""
    _project(tmp_path)
    result = subprocess.run(
        [sys.executable, "-c", """
import sys
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured
assert not settings.configured
try:
    settings.SECRET_KEY
except ImproperlyConfigured as error:
    assert "unavailable-dependency" in str(error), str(error)
else:
    raise AssertionError("Composition must reject the unknown dependency")
assert not settings.configured
assert "settings" not in sys.modules
"""],
        cwd=tmp_path, env={**_environment(tmp_path), "DJANGO_COMPOSE_MODE": "write"},
        capture_output=True, text=True, check=False,
    )
    assert result.returncode == 0, result.stderr


def test_money_config_discovery_does_not_configure_settings(tmp_path: Path) -> None:
    environment = _environment(tmp_path)
    environment.pop("DJANGO_SETTINGS_MODULE")
    result = subprocess.run(
        [sys.executable, "-c", "from django.apps import AppConfig; from django.conf import settings; "
         "assert not settings.configured; AppConfig.create('angee.money'); assert not settings.configured"],
        cwd=tmp_path, env=environment, capture_output=True, text=True, check=False,
    )
    assert result.returncode == 0, result.stderr
