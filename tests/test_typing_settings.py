"""Autoconfig declaration types survive native mypy caches and composition."""

from __future__ import annotations

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
