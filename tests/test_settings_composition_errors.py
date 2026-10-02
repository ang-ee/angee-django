"""Settings publication and app discovery retain the original composition error."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

from angee.project import PROJECT_DIR_ENV, PROJECT_SETTINGS_ENV


def _project(tmp_path: Path) -> None:
    """Declare one valid root before an addon with a missing dependency."""

    addon = tmp_path / "addons" / "missing_dependency"
    addon.mkdir(parents=True)
    (addon / "__init__.py").write_text("", encoding="utf-8")
    (addon / "addon.toml").write_text(
        '[addon]\nname = "missing_dependency"\ndepends_on = ["unavailable_dependency"]\n', encoding="utf-8",
    )
    (tmp_path / "settings.yaml").write_text(
        "SECRET_KEY: isolated-composition-test\n"
        "INSTALLED_APPS: [angee.money, missing_dependency]\n"
        'ANGEE_RUNTIME_DIR: "{BASE_DIR}/runtime"\n',
        encoding="utf-8",
    )


def _environment(tmp_path: Path) -> dict[str, str]:
    """Inherit the runner's environment while selecting this isolated project."""

    environment = dict(os.environ)
    python_path = [str(Path(__file__).resolve().parents[1] / "addons")]
    if environment.get("PYTHONPATH"):
        python_path.append(environment["PYTHONPATH"])
    environment.update({
        PROJECT_DIR_ENV: str(tmp_path),
        PROJECT_SETTINGS_ENV: "settings",
        "DJANGO_SETTINGS_MODULE": "angee.compose.settings",
        "PYTHONPATH": os.pathsep.join(python_path),
    })
    return environment


def test_management_startup_reports_the_unknown_dependency(tmp_path: Path) -> None:
    """Django's initial settings probe cannot replace the failure with an auth error."""

    _project(tmp_path)
    result = subprocess.run(
        [
            sys.executable, "-c",
            "from django.core.management import execute_from_command_line; "
            "execute_from_command_line(['manage.py', 'check'])",
        ],
        cwd=Path(__file__).resolve().parents[1], env=_environment(tmp_path),
        capture_output=True, text=True, check=False,
    )
    assert result.returncode != 0
    assert "depends on unknown app 'unavailable_dependency'" in result.stderr
    assert "django.contrib.auth" not in result.stderr
    assert "doesn't declare an explicit app_label" not in result.stderr


def test_failed_composition_preserves_published_settings(tmp_path: Path) -> None:
    """A failed reload publishes neither defaults nor a partly resolved app list."""

    _project(tmp_path)
    result = subprocess.run(
        [
            sys.executable, "-c",
            """
from django.core.exceptions import ImproperlyConfigured
from angee.compose.project import ProjectContract

published = {"__name__": "published_settings", "INSTALLED_APPS": ["retained"], "RETAINED": True}
previous = dict(published)
try:
    ProjectContract(published).compose()
except ImproperlyConfigured as error:
    assert "depends on unknown app 'unavailable_dependency'" in str(error), str(error)
else:
    raise AssertionError("Composition must reject the unknown dependency")
assert published == previous
""",
        ],
        cwd=Path(__file__).resolve().parents[1], env=_environment(tmp_path),
        capture_output=True, text=True, check=False,
    )
    assert result.returncode == 0, result.stderr


def test_money_config_discovery_does_not_configure_settings(tmp_path: Path) -> None:
    """Native AppConfig inspection can run before a Django settings module exists."""

    environment = _environment(tmp_path)
    environment.pop("DJANGO_SETTINGS_MODULE")
    result = subprocess.run(
        [
            sys.executable, "-c",
            "from django.apps import AppConfig; from django.conf import settings; "
            "assert not settings.configured; AppConfig.create('angee.money'); assert not settings.configured",
        ],
        cwd=Path(__file__).resolve().parents[1], env=environment,
        capture_output=True, text=True, check=False,
    )
    assert result.returncode == 0, result.stderr
