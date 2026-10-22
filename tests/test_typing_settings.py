"""Autoconfig declaration types survive native mypy caches and composition."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

_DEFAULTS = """\
from typing import TypedDict
from collections.abc import Mapping
_Defaults = TypedDict('_Defaults', {
    'ANGEE_COUNT': int, 'ANGEE_MAYBE': str | None, 'ANGEE_VALUES': list[str],
})
SETTINGS: _Defaults = {'ANGEE_COUNT': 17, 'ANGEE_MAYBE': None, 'ANGEE_VALUES': ['base']}
class _Derived(TypedDict):
    ANGEE_DERIVED: str | None

def settings(namespace: Mapping[str, object]) -> _Derived:
    return {'ANGEE_DERIVED': None}
"""


def test_declared_settings_types_preserve_native_errors_and_warm_dependencies(tmp_path: Path) -> None:
    root = Path(__file__).resolve().parents[1]
    for name in ("settings_one", "settings_two"):
        package = tmp_path / name
        package.mkdir()
        (package / "__init__.py").write_text("")
    source = tmp_path / "settings_one/autoconfig.py"
    source.write_text(_DEFAULTS)
    (tmp_path / "settings_two/autoconfig.py").write_text(
        "from typing import TypedDict\n"
        "_Defaults = TypedDict('_Defaults', {'ANGEE_VALUES:append': list[str]})\n"
        "SETTINGS: _Defaults = {'ANGEE_VALUES:append': ['extra']}\n"
    )
    (tmp_path / "fixture_settings.py").write_text(
        "from angee.compose.autoconfig import AutoConfig\n"
        "SECRET_KEY = 'settings-types'\n"
        "INSTALLED_APPS = ['django.contrib.contenttypes', 'settings_one', 'settings_two']\n"
        "DATABASES = {'default': {'ENGINE': 'django.db.backends.sqlite3', 'NAME': ':memory:'}}\n"
        "AutoConfig.apply_installed(globals(), environment=False)\n"
    )
    config = tmp_path / "mypy.ini"
    config.write_text(
        "[mypy]\nplugins = angee.compose.typing\n"
        f"mypy_path = {tmp_path},{root},{root / 'addons'}\ncache_dir = {tmp_path / 'cache'}\n"
        "ignore_missing_imports = True\n"
        "[mypy.plugins.django-stubs]\ndjango_settings_module = fixture_settings\n"
    )
    valid = tmp_path / "valid.py"
    valid.write_text(
        "from typing import assert_type\nfrom django.conf import settings\n"
        "assert_type(settings.ANGEE_COUNT, int)\n"
        "assert_type(settings.ANGEE_MAYBE, str | None)\n"
        "assert_type(settings.ANGEE_DERIVED, str | None)\n"
        "assert_type(settings.ANGEE_VALUES, list[str])\n"
    )
    invalid = tmp_path / "invalid.py"
    invalid.write_text(
        "from django.conf import settings\n"
        "wrong: str = settings.ANGEE_COUNT\n"
        "wrong_nullable: int = settings.ANGEE_MAYBE\n"
        "wrong_derived: int = settings.ANGEE_DERIVED\n"
        "settings.ANGEE_UNKNOWN\n"
    )
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join(filter(None, (str(tmp_path), str(root), env.get("PYTHONPATH"))))
    env["DJANGO_SETTINGS_MODULE"] = "fixture_settings"

    def check(path: Path) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [sys.executable, "-m", "mypy", "--config-file", str(config), str(path)],
            cwd=root, env=env, capture_output=True, text=True, check=False,
        )

    for _ in range(2):
        positive = check(valid)
        assert positive.returncode == 0, positive.stdout + positive.stderr
    negative = check(invalid)
    assert negative.returncode == 1, negative.stdout + negative.stderr
    assert negative.stdout.count(": error:") == 4, negative.stdout
    for line in (2, 3, 4, 5):
        assert f"invalid.py:{line}: error:" in negative.stdout, negative.stdout

    source.write_text(_DEFAULTS.replace("'ANGEE_COUNT': int", "'ANGEE_COUNT': str")
                      .replace("'ANGEE_COUNT': 17", "'ANGEE_COUNT': 'changed'"))
    changed = check(valid)
    assert changed.returncode == 1, changed.stdout + changed.stderr
    assert 'Expression is of type "str", not "int"' in changed.stdout
    source.write_text(_DEFAULTS.replace("'ANGEE_COUNT': int, ", "").replace("'ANGEE_COUNT': 17, ", ""))
    removed = check(valid)
    assert removed.returncode == 1, removed.stdout + removed.stderr
    assert "has no attribute 'ANGEE_COUNT'" in removed.stdout
    source.write_text(_DEFAULTS)
    restored = check(valid)
    assert restored.returncode == 0, restored.stdout + restored.stderr


def _typing_host_probe(root: Path, project: Path | None) -> subprocess.CompletedProcess[str]:
    env = dict(os.environ)
    env.pop("ANGEE_PROJECT_DIR", None)
    if project is not None:
        env["ANGEE_PROJECT_DIR"] = str(project)
    env["PYTHONPATH"] = str(root)
    return subprocess.run([
        sys.executable, "-c", "\n".join([
            "import json",
            "from pathlib import Path",
            "from tests import typing_settings as host",
            "print(json.dumps({",
            "'apps': [getattr(app, 'name', app) for app in host.INSTALLED_APPS],",
            "'addon_dirs': [str(path) for path in host.ANGEE_ADDON_DIRS],",
            "'runtime': str(host.ANGEE_RUNTIME_DIR),",
            "'database': host.DATABASES['default'], 'secret': host.SECRET_KEY,",
            "}))",
        ]),
    ], cwd=root, env=env, capture_output=True, text=True, check=False)


def test_typing_host_keeps_default_framework_composition() -> None:
    root = Path(__file__).resolve().parents[1]
    result = _typing_host_probe(root, None)
    assert result.returncode == 0, result.stdout + result.stderr
    host = json.loads(result.stdout)
    assert "angee.money" in host["apps"]
    assert "angee.workflows" in host["apps"]
    assert "tests.typing_host.TypingHostConfig" in host["apps"]
    assert host["addon_dirs"] == [str(root / "addons")]
    assert host["runtime"] == str(root / ".mypy_cache/typing_runtime")
    assert host["database"] == {"ENGINE": "django.db.backends.sqlite3", "NAME": ":memory:"}


def test_typing_host_uses_declared_project_paths_without_live_database_or_runtime(tmp_path: Path) -> None:
    root = Path(__file__).resolve().parents[1]
    project = tmp_path / "project"
    project.mkdir()
    addon_dir = tmp_path / "consumer_addons"
    addon = addon_dir / "typing_consumer"
    addon.mkdir(parents=True)
    (addon / "__init__.py").write_text("")
    (addon / "apps.py").write_text(
        "from django.apps import AppConfig\n"
        "class ConsumerConfig(AppConfig):\n    name = 'typing_consumer'\n    default = True\n"
    )
    (addon / "addon.toml").write_text(
        '[addon]\nname = "typing_consumer"\ndepends_on = ["angee.money"]\n'
    )
    (project / "settings.py").write_text(
        "from pathlib import Path\n"
        "BASE_DIR = Path(__file__).parent\n"
        "INSTALLED_APPS = ['typing_consumer']\n"
        f"ANGEE_ADDON_DIRS = [Path({str(addon_dir)!r}), Path({str(root / 'addons')!r})]\n"
        "SECRET_KEY = 'project-secret-must-not-survive'\n"
        "ANGEE_RUNTIME_DIR = BASE_DIR / 'live_runtime'\n"
        "DATABASES = {'default': {'ENGINE': 'django.db.backends.postgresql', 'NAME': 'do-not-connect'}}\n"
    )
    result = _typing_host_probe(root, project)
    assert result.returncode == 0, result.stdout + result.stderr
    host = json.loads(result.stdout)
    assert "angee.money" in host["apps"]
    assert "typing_consumer" in host["apps"]
    assert "angee.workflows" not in host["apps"]
    assert "tests.typing_host.TypingHostConfig" in host["apps"]
    assert host["addon_dirs"] == [str(addon_dir), str(root / "addons")]
    assert host["runtime"] == str(project / ".mypy_cache/typing_runtime")
    assert host["database"] == {"ENGINE": "django.db.backends.sqlite3", "NAME": ":memory:"}
    assert host["secret"] == "framework-type-checking"
    assert not (project / "live_runtime").exists()


def test_explicit_typing_project_never_silently_falls_back_to_framework(tmp_path: Path) -> None:
    result = _typing_host_probe(Path(__file__).resolve().parents[1], tmp_path)
    assert result.returncode != 0
    assert "needs settings.py or settings.yaml" in result.stderr


def test_immutable_migration_imports_retain_typed_caller_errors(tmp_path: Path) -> None:
    root = Path(__file__).resolve().parents[1]
    package = tmp_path / "historical_fixture"
    migrations = package / "runtime_migrations"
    migrations.mkdir(parents=True)
    (package / "__init__.py").write_text("")
    (migrations / "__init__.py").write_text("")
    (migrations / "retained.py").write_text(
        "value: int = 'historical diagnostic must stay untouched'\n"
        "def forward(value: int) -> int:\n    return value\n"
    )
    (package / "caller.py").write_text(
        "from historical_fixture.runtime_migrations.retained import forward\n"
        "valid: int = forward(1)\ninvalid: str = forward(1)\n"
    )
    env = dict(os.environ, PYTHONPATH=str(root), MYPYPATH=str(tmp_path))
    env.pop("ANGEE_PROJECT_DIR", None)
    result = subprocess.run([
        sys.executable, "-m", "mypy", "--config-file", str(root / "pyproject.toml"),
        "--cache-dir", str(tmp_path / "cache"), str(package),
    ], cwd=root, env=env, capture_output=True, text=True, check=False)
    assert result.returncode == 1, result.stdout + result.stderr
    assert result.stdout.count(": error:") == 1, result.stdout
    assert "caller.py:3: error:" in result.stdout
    assert "retained.py" not in result.stdout
