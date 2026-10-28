"""Role anchors remain classes when generated modules import deferred aliases."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


def test_generated_role_anchor_alias_survives_cold_and_warm_analysis(tmp_path: Path) -> None:
    root = Path(__file__).resolve().parents[1]
    app = tmp_path / "role_probe"
    app.mkdir()
    (app / "__init__.py").write_text("")
    declaration = app / "models.py"
    declaration.write_text(
        "from django.conf import settings\n"
        "from angee.base.models import role_anchor\n"
        "ProbeRole = role_anchor('probe/role')\n"
    )
    (tmp_path / "role_settings.py").write_text(
        "from pathlib import Path\nSECRET_KEY = 'role-types'\n"
        "INSTALLED_APPS = ['django.contrib.contenttypes', 'django.contrib.auth', 'rebac', "
        "'angee', 'angee.base', 'role_probe', 'angee.compose']\n"
        "DATABASES = {'default': {'ENGINE': 'django.db.backends.sqlite3', 'NAME': ':memory:'}}\n"
        "DEFAULT_AUTO_FIELD = 'django.db.models.BigAutoField'\n"
        f"COMPOSE_RUNTIME_DIR = Path({str(tmp_path / 'role_runtime')!r})\n"
        "COMPOSE_RUNTIME_MODULE = 'role_runtime'\n"
    )
    config = tmp_path / "mypy.ini"
    config.write_text(
        "[mypy]\nplugins = angee.base.typing\n"
        f"mypy_path = {tmp_path},{root},{root / 'addons'}\ncache_dir = {tmp_path / 'cache'}\n"
        "[mypy.plugins.django-stubs]\ndjango_settings_module = role_settings\n"
    )
    env = dict(os.environ)
    env["PYTHONPATH"] = os.pathsep.join(
        filter(None, (str(tmp_path), str(root), str(root / "addons"), env.get("PYTHONPATH")))
    )
    env["DJANGO_SETTINGS_MODULE"] = "role_settings"
    bootstrap = subprocess.run(
        [sys.executable, "-c", "import django; django.setup()"],
        cwd=root, env=env, capture_output=True, text=True, check=False,
    )
    assert bootstrap.returncode == 0, bootstrap.stdout + bootstrap.stderr
    generated = tmp_path / "role_runtime/role_probe/models.py"
    assert "class ProbeRole(AbstractProbeRole):" in generated.read_text()
    command = [sys.executable, "-m", "mypy", "--show-traceback", "--config-file", str(config)]

    def check(*paths: Path) -> subprocess.CompletedProcess[str]:
        return subprocess.run(
            [*command, *(str(path) for path in paths)], cwd=root, env=env,
            capture_output=True, text=True, check=False,
        )

    # Making the generated import a primary target exposes semantic deferral;
    # checking only the source declaration can hide the stale imported alias.
    for _ in range(2):
        result = check(generated)
        assert result.returncode == 0, result.stdout + result.stderr
    source = check(declaration)
    assert source.returncode == 0, source.stdout + source.stderr

    consumer = tmp_path / "consumer.py"
    consumer.write_text(
        "from role_probe.models import ProbeRole as AbstractProbeRole\n"
        "from role_runtime.role_probe.models import ProbeRole\n"
        "def valid(row: ProbeRole) -> AbstractProbeRole:\n    return row\n"
        "def invalid(row: ProbeRole) -> str:\n    return row\n"
        "ProbeRole.missing()\n"
    )
    negative = check(consumer)
    assert negative.returncode == 1, negative.stdout + negative.stderr
    assert negative.stdout.count(": error:") == 2, negative.stdout
    assert "[return-value]" in negative.stdout
    assert "[attr-defined]" in negative.stdout
