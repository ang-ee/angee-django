"""Native host metadata preparation before addon requirements are installed."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tomllib
from dataclasses import replace
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]


def test_hatch_plugin_import_has_no_django_or_sdk_lifecycle() -> None:
    """PEP 517 can discover the plugin before loading project declarations."""

    result = subprocess.run(
        [sys.executable, "-c", "\n".join((
            "import sys",
            "import angee.hatch as plugin",
            "assert plugin.hatch_register_metadata_hook() is plugin.RuntimeMetadataHook",
            "assert plugin.hatch_register_build_hook() is plugin.RuntimeBuildHook",
            "assert plugin.RuntimeMetadataHook.PLUGIN_NAME == 'angee-runtime'",
            "assert 'django' not in sys.modules",
            "assert 'sentry_sdk' not in sys.modules",
            "assert 'angee.conf' not in sys.modules",
        ))],
        cwd=ROOT, capture_output=True, text=True, timeout=30, check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_native_hatch_metadata_uses_one_project_closure_without_app_imports(tmp_path: Path) -> None:
    """Requirements and anchors share precedence/selection, with native dynamic validation."""

    root = tmp_path / "host"
    roots = (root / "addons", root / "other-addons")
    for addon_root, name, dependencies, depends_on in (
        (roots[0], "angee.base", ["base-lib>=1"], []),
        (roots[0], "example.parent", ["parent-lib>=2", "shared-lib>=1"], []),
        (roots[0], "example.enabled", ["shared-lib>=1"], ["example.parent"]),
        (roots[0], "example.disabled", ["disabled-sdk>=99"], []),
        (roots[1], "example.enabled", ["shadow-sdk>=99"], []),
    ):
        directory = addon_root.joinpath(*name.split("."))
        directory.mkdir(parents=True)
        (directory / "addon.toml").write_text(
            f"[addon]\nname = {json.dumps(name)}\n"
            f"dependencies = {json.dumps(dependencies)}\n"
            f"depends_on = {json.dumps(depends_on)}\n",
            encoding="utf-8",
        )
        for filename in ("__init__.py", "models.py"):
            (directory / filename).write_text(
                'raise AssertionError("metadata must not import addon code or SDKs")\n', encoding="utf-8",
            )
    (root / "settings.yaml").write_text(
        'SECRET_KEY: metadata-only\nINSTALLED_APPS: [example.enabled]\n'
        'ANGEE_ADDON_DIRS: ["{BASE_DIR}/addons", "{BASE_DIR}/other-addons"]\n'
        'COMPOSE_RUNTIME_DIR: "{BASE_DIR}/runtime"\n',
        encoding="utf-8",
    )
    (root / "pyproject.toml").write_text(
        '[project]\nname = "metadata-proof"\nversion = "0.0.0"\n'
        'dynamic = ["dependencies", "entry-points"]\n\n'
        '[tool.hatch.metadata.hooks.angee-runtime]\n'
        'dependencies = ["seed-package>=1", "shared-lib>=1"]\n',
        encoding="utf-8",
    )
    foreign = tmp_path / "foreign"
    foreign.mkdir()
    (foreign / "settings.yaml").write_text("SECRET_KEY: wrong-root\n", encoding="utf-8")
    env = {
        name: value for name, value in os.environ.items()
        if not name.startswith("YAMLCONF_")
        and name not in {"DJANGO_SETTINGS_MODULE", "ANGEE_PROJECT_SETTINGS", "DATABASE_URL", "SENTRY_DSN"}
    }
    env["ANGEE_PROJECT_DIR"] = str(foreign)
    result = subprocess.run(
        [sys.executable, "-c", r'''
import copy
import json
import sys
from pathlib import Path

import angee.hatch as plugin
from hatchling.metadata.core import ProjectMetadata
from hatchling.plugin.manager import PluginManager

root = Path(sys.argv[1])
source = {path: path.read_bytes() for path in root.rglob("*") if path.is_file()}
manager = PluginManager()
manager.manager.register(plugin, name="angee")
project = ProjectMetadata(str(root), manager)
core = project.core
project.validate_fields()
assert core.dependencies == ["base-lib>=1", "parent-lib>=2", "seed-package>=1", "shared-lib>=1"]
assert core.entry_points == {"angee.addons": {
    "angee.base": "angee.base",
    "example.enabled": "example.enabled",
    "example.parent": "example.parent",
}}
from django.apps import apps
assert not apps.apps_ready and not apps.ready
assert "sentry_sdk" not in sys.modules
assert not any(name.startswith(("example.", "angee.base", "angee.compose.runtime")) for name in sys.modules)
assert all(path.read_bytes() == contents for path, contents in source.items())
assert not (root / "runtime").exists()

# Native Hatch owns PEP 621 dynamic/static refusal, rather than a second parser.
for field, value in (("dependencies", ["static-lib>=1"]), ("entry-points", {"other": {"item": "other"}})):
    configured = copy.deepcopy(project.config)
    configured["project"][field] = value
    invalid = ProjectMetadata(str(root), manager, config=configured)
    try:
        getattr(invalid.core, "dependencies" if field == "dependencies" else "entry_points")
    except ValueError as error:
        assert "cannot be both statically defined" in str(error)
    else:
        raise AssertionError(f"native metadata accepted static and dynamic {field}")

print(json.dumps({"dependencies": core.dependencies, "entry-points": core.entry_points}))
''', str(root)],
        cwd=ROOT, env=env, capture_output=True, text=True, timeout=30, check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert "disabled-sdk" not in result.stdout
    assert "shadow-sdk" not in result.stdout
    assert json.loads(result.stdout)["entry-points"]["angee.addons"] == {
        "angee.base": "angee.base", "example.enabled": "example.enabled", "example.parent": "example.parent",
    }


def test_host_snapshot_is_portable_authored_source_not_effective_deployment_settings(tmp_path: Path) -> None:
    """The sdist keeps native history/namespace behavior and never copies .env."""

    import yaml
    from hatch_angee import AddonManifest

    from angee.hatch import _stage_project

    root = tmp_path / "host"
    root.mkdir()
    addon = tmp_path / "checkout" / "example" / "enabled"
    addon.mkdir(parents=True)
    (addon.parent / "__init__.py").write_text('NAMESPACE = "authored"\n', encoding="utf-8")
    (addon / "__init__.py").write_text("", encoding="utf-8")
    (addon / "addon.toml").write_text('[addon]\nname="example.enabled"\n', encoding="utf-8")
    (addon / "permissions.zed").write_text("definition example/enabled {}\n", encoding="utf-8")
    (addon / ".env").write_text("SECRET=addon-canary\n", encoding="utf-8")
    (root / ".env").write_text("SECRET=host-canary\n", encoding="utf-8")
    project = (
        '[build-system]\nrequires=["hatchling>=1.27", "django-angee>=0.3"]\nbuild-backend="hatchling.build"\n'
        '[project]\nname="snapshot"\nversion="0"\n'
        '[tool.uv.sources]\ndjango-angee={path="../checkout",editable=true}\n'
        '[tool.uv.workspace]\nmembers=["../another-checkout"]\n'
    )
    (root / "pyproject.toml").write_text(project, encoding="utf-8")
    checkout = addon.parents[1]
    (checkout / "pyproject.toml").write_text(
        '[project]\nname="addon-source"\nversion="0"\n'
        '[tool.hatch.build.targets.sdist]\n'
        'only-include=["different-distribution-root"]\nexclude=["example/enabled/ignored-hatch.txt"]\n',
        encoding="utf-8",
    )
    (addon / ".DS_Store").write_bytes(b"native-excluded")
    (checkout / ".gitignore").write_text("example/enabled/ignored-native.txt\n", encoding="utf-8")
    (addon / "ignored-native.txt").write_text("native VCS exclusion\n", encoding="utf-8")
    (addon / "ignored-hatch.txt").write_text("native Hatch source exclusion\n", encoding="utf-8")
    (addon / "tool.sh").write_text("#!/bin/sh\nexit 0\n", encoding="utf-8")
    (addon / "tool.sh").chmod(0o755)
    raw_yaml = "SECRET_KEY: '{SECRET_KEY}'\nDEBUG: false\nINSTALLED_APPS: [example.disabled]\n"
    (root / "settings.yaml").write_text(raw_yaml, encoding="utf-8")
    history = root / "runtime" / "enabled" / "migrations"
    history.mkdir(parents=True)
    (history / "__init__.py").write_text("", encoding="utf-8")
    migration = "from django.db import migrations\nclass Migration(migrations.Migration):\n    operations = []\n"
    (history / "0001_initial.py").write_text(migration, encoding="utf-8")
    stage = tmp_path / "stage"
    stage.mkdir()
    manifest = AddonManifest(name="example.enabled", permissions="permissions.zed")
    _stage_project(
        root, stage, ((addon, manifest),),
        {"COMPOSE_RUNTIME_MODULE": "runtime", "COMPOSE_RUNTIME_DIR": root / "runtime", "SECRET_KEY": "resolved-canary"},
        ("example.enabled",),
    )
    assert yaml.safe_load((stage / "settings.yaml").read_text()) == {
        "SECRET_KEY": "{SECRET_KEY}", "DEBUG": False,
        "INSTALLED_APPS": ["example.enabled"], "ANGEE_ADDON_DIRS": ["{SOURCE_DIR}"],
        "COMPOSE_RUNTIME_MODULE": "runtime", "COMPOSE_RUNTIME_DIR": "{BASE_DIR}/runtime",
    }
    assert (stage / "example/__init__.py").read_text() == 'NAMESPACE = "authored"\n'
    assert (stage / "runtime/enabled/migrations/0001_initial.py").read_text() == migration
    assert (root / "settings.yaml").read_text() == raw_yaml
    assert (root / "pyproject.toml").read_text() == project
    staged_project = tomllib.loads((stage / "pyproject.toml").read_text())
    assert staged_project["build-system"] == {
        "requires": ["hatchling>=1.27", "django-angee>=0.3"], "build-backend": "hatchling.build",
    }
    assert "uv" not in staged_project.get("tool", {})
    assert not (stage / "example/enabled/.DS_Store").exists()
    assert not (stage / "example/enabled/ignored-native.txt").exists()
    assert not (stage / "example/enabled/ignored-hatch.txt").exists()
    assert (stage / "example/enabled/tool.sh").stat().st_mode & 0o111
    assert not list(stage.rglob(".env"))
    assert "resolved-canary" not in "".join(path.read_text() for path in stage.rglob("*") if path.is_file())
    with pytest.raises(ValueError, match="outside the bundled addon"):
        _stage_project(
            root, stage, ((addon, replace(manifest, permissions=str(tmp_path / "foreign.zed"))),),
            {"COMPOSE_RUNTIME_MODULE": "runtime"}, ("example.enabled",),
        )


def test_native_host_wheel_from_sdist_has_sealed_history_and_imports_without_checkout(tmp_path: Path) -> None:
    """Exercise native Hatch archives and Django authoring/startup, without an installer or DB."""

    root = tmp_path / "host"
    root.mkdir()
    source = tmp_path / "sources"
    addon = source / "portable" / "notes"
    addon.mkdir(parents=True)
    (addon / "__init__.py").write_text("", encoding="utf-8")
    (addon / "addon.toml").write_text('[addon]\nname="portable.notes"\n', encoding="utf-8")
    (addon / "models.py").write_text(
        "from django.db import models\nfrom host_helpers import TITLE_LIMIT\n\nclass Note(models.Model):\n"
        "    runtime = True\n    title = models.CharField(max_length=TITLE_LIMIT)\n"
        "    class Meta:\n        abstract = True\n",
        encoding="utf-8",
    )
    (root / "host_helpers.py").write_text(
        'TITLE_LIMIT = 80\nROOT_APPS = ["django.contrib.auth", "portable.notes"]\n', encoding="utf-8",
    )
    (root / "settings.py").write_text(
        'from host_helpers import ROOT_APPS\nINSTALLED_APPS = ROOT_APPS\nSECRET_KEY = "authored-placeholder"\n',
        encoding="utf-8",
    )
    (root / "settings.yaml").write_text(
        f"ANGEE_ADDON_DIRS: {json.dumps([str(ROOT / 'addons'), str(source)])}\n"
        'COMPOSE_RUNTIME_DIR: "{BASE_DIR}/runtime"\n'
        "DATABASES:\n  default:\n    ENGINE: django.db.backends.dummy\n",
        encoding="utf-8",
    )
    (root / "pyproject.toml").write_text(
        '[project]\nname="portable-host"\nversion="0.0.0"\ndynamic=["dependencies", "entry-points"]\n'
        '[tool.hatch.metadata]\nallow-direct-references=true\n'
        '[tool.hatch.metadata.hooks.angee-runtime]\ndependencies=[]\n'
        '[tool.hatch.build.hooks.angee-runtime]\nrequire-runtime-dependencies=true\n'
        '[tool.hatch.build.targets.wheel]\nbypass-selection=true\n',
        encoding="utf-8",
    )
    env = {
        name: value for name, value in os.environ.items()
        if not name.startswith(("ANGEE_", "YAMLCONF_"))
        and name not in {"DJANGO_SETTINGS_MODULE", "DATABASE_URL", "SENTRY_DSN", "PYTHONPATH"}
    }
    env["PYTHONPATH"] = str(ROOT)
    result = subprocess.run(
        [sys.executable, "-c", r"""
import os
import shutil
import subprocess
import sys
import tarfile
import zipfile
from pathlib import Path

from hatchling.builders.sdist import SdistBuilder
from hatchling.builders.wheel import WheelBuilder
from hatchling.plugin.manager import PluginManager
import angee.hatch as plugin

root, source = map(Path, sys.argv[1:])
original = {path: path.read_bytes() for path in root.rglob('*') if path.is_file()}
manager = PluginManager()
manager.manager.register(plugin, name='angee')
dist = root.parent / 'dist'
dist.mkdir()

# An implicit current-directory package must not replace the installed build
# plugin before its explicit project source paths are configured.
poison = root / 'angee.py'
poison.write_text("raise AssertionError('cwd shadowed installed build plugin')\n")
# Editable emits models for native makemigrations, without inventing initial
# migration files or installing copies of addon Python into site-packages.
editable = Path(next(WheelBuilder(str(root), plugin_manager=manager).build(
    directory=str(dist), versions=['editable'],
)))
assert (root / 'runtime/notes/models.py').is_file()
assert sorted(path.name for path in (root / 'runtime/notes/migrations').iterdir()) == ['__init__.py']
with zipfile.ZipFile(editable) as archive:
    assert not any(name.startswith(('portable/', 'angee/base/')) for name in archive.namelist())
    paths = archive.read(next(name for name in archive.namelist() if name.endswith('.pth'))).decode()
    assert str(source) in paths and str(root) in paths
poison.unlink()

try:
    next(WheelBuilder(str(root), plugin_manager=manager).build(directory=str(dist)))
except subprocess.CalledProcessError:
    pass
else:
    raise AssertionError('normal wheel accepted unsealed native history')
assert not list((root / 'runtime/notes/migrations').glob('0*.py'))

author = '''
import os, sys
from django.core.management import execute_from_command_line
os.environ['ANGEE_PROJECT_DIR'] = sys.argv[1]
os.environ['DJANGO_SETTINGS_MODULE'] = 'runtime.settings'
sys.path.insert(0, sys.argv[1])
execute_from_command_line(['manage.py', 'makemigrations', '--noinput', '--skip-checks'])
'''
subprocess.run([sys.executable, '-c', author, str(root)], check=True, cwd=root)
history = root / 'runtime/notes/migrations/0001_initial.py'
assert history.is_file()
history_bytes = history.read_bytes()
wheel = Path(next(WheelBuilder(str(root), plugin_manager=manager).build(directory=str(dist))))
sdist = Path(next(SdistBuilder(str(root), plugin_manager=manager).build(directory=str(dist))))
assert history.read_bytes() == history_bytes
assert all(path.read_bytes() == contents for path, contents in original.items())

unpacked = root.parent / 'unpacked'
with tarfile.open(sdist) as archive:
    archive.extractall(unpacked, filter='data')
sdist_root = next(unpacked.iterdir())
assert (sdist_root / 'portable/notes/addon.toml').is_file()
assert (sdist_root / 'runtime/notes/migrations/0001_initial.py').read_bytes() == history_bytes
shutil.rmtree(root)
shutil.rmtree(source)
rebuilt_dist = dist / 'rebuilt'
rebuilt_dist.mkdir()
rebuilt = Path(next(WheelBuilder(str(sdist_root), plugin_manager=manager).build(directory=str(rebuilt_dist))))
with zipfile.ZipFile(wheel) as direct, zipfile.ZipFile(rebuilt) as indirect:
    for name in direct.namelist():
        if name.startswith(('runtime/', 'portable/', 'migrations/')):
            assert direct.read(name) == indirect.read(name), name
        if name.startswith('runtime/') and name.endswith('.py'):
            assert str(root.parent).encode() not in direct.read(name), name
    installed = root.parent / 'installed'
    indirect.extractall(installed)
assert (installed / 'runtime/settings.yaml').is_file()
assert (installed / 'host_helpers.py').is_file()

deployment = root.parent / 'deployment'
deployment.mkdir()
(deployment / 'settings.yaml').write_text('SECRET_KEY: deployment-only\n')
(deployment / 'settings.py').write_text("raise AssertionError('deployment settings.py replaced authored module')\n")
serve = '''
import os, sys
sys.path.insert(0, sys.argv[1])
os.environ['ANGEE_PROJECT_DIR'] = sys.argv[2]
os.environ['DJANGO_SETTINGS_MODULE'] = 'runtime.settings'
import django
django.setup()
from django.apps import apps
from django.conf import settings
model = apps.get_model('notes', 'Note')
assert model._meta.get_field('title').max_length == 80
assert settings.SECRET_KEY == 'deployment-only'
assert settings.MIGRATION_MODULES['notes'] == 'runtime.notes.migrations'
from django.db import connections
assert all(connection.connection is None for connection in connections.all())
'''
subprocess.run([sys.executable, '-c', serve, str(installed), str(deployment)], cwd=deployment, check=True)
""", str(root), str(source)],
        cwd=ROOT, env=env, capture_output=True, text=True, timeout=120, check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
