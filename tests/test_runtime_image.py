"""Regression coverage for the published runtime image contract."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
DOCKERFILE = ROOT / "Dockerfile"
ENTRYPOINT = ROOT / "docker" / "runtime-entrypoint.sh"


def test_runtime_image_prepares_bind_mount_outputs_before_dropping_privileges() -> None:
    dockerfile = DOCKERFILE.read_text(encoding="utf-8")
    entrypoint = ENTRYPOINT.read_text(encoding="utf-8")

    assert "gosu" in dockerfile
    assert "COPY docker/runtime-entrypoint.sh /usr/local/bin/angee-django-entrypoint" in dockerfile
    assert 'ENTRYPOINT ["tini", "--", "/usr/local/bin/angee-django-entrypoint"]' in dockerfile
    assert 'mkdir -p "$project_dir/runtime" "$project_dir/data"' in entrypoint
    assert 'chown -R angee:angee "$project_dir/runtime"' in entrypoint
    assert 'chown angee:angee "$project_dir/data"' in entrypoint
    assert 'chown -R angee:angee "$project_dir/runtime" "$project_dir/data"' not in entrypoint
    assert "-exec chown -R angee:angee" not in entrypoint
    assert "find /app/data" not in entrypoint
    assert 'exec gosu angee "$@"' in entrypoint


@pytest.mark.parametrize("custom_overlay", [False, True])
def test_baked_image_addon_root_uses_native_yamlconf_append(tmp_path: Path, custom_overlay: bool) -> None:
    """The baked environment extends project discovery before Django app loading."""

    (tmp_path / "settings.yaml").write_text('SECRET_KEY: image-probe\nANGEE_ADDON_DIRS:\n  - "{BASE_DIR}/addons"\n')
    overlay = ROOT / "docker" / "addon-settings.yaml"
    expected_root = "/opt/angee-src/addons"
    if custom_overlay:
        overlay = tmp_path / "deployment.yaml"
        overlay.write_text('ANGEE_ADDON_DIRS:append:\n  - "{BASE_DIR}/deployment-addons"\n')
        expected_root = str(tmp_path / "deployment-addons")
    result = subprocess.run(
        ["sh", "-c", 'exec "$@"', "image-environment", sys.executable, "-c", (
            "import json; from angee.conf import ProjectContract; "
            "namespace={}; ProjectContract(namespace).load(); "
            "print(json.dumps([str(path) for path in namespace['ANGEE_ADDON_DIRS']]))"
        )],
        env={
            **os.environ, "ANGEE_PROJECT_DIR": str(tmp_path),
            "YAMLCONF_CONFFILE": str(overlay),
        },
        capture_output=True, text=True, check=True, timeout=30,
    )
    assert json.loads(result.stdout) == [str(tmp_path / "addons"), expected_root]
    dockerfile = DOCKERFILE.read_text()
    assert "COPY docker/addon-settings.yaml ./addon-settings.yaml" in dockerfile
    assert "ENV YAMLCONF_CONFFILE=/opt/angee-src/addon-settings.yaml" in dockerfile


def test_web_image_uses_native_package_sources_and_build_context() -> None:
    """The web image consumes source package manifests instead of wheel contents."""

    dockerfile = DOCKERFILE.read_text()
    ignored = (ROOT / ".dockerignore").read_text().splitlines()
    assert "packages" not in ignored
    assert "COPY packages ./packages" in dockerfile
    assert "COPY addons ./addons" in dockerfile
    assert "COPY package.json pnpm-workspace.yaml pnpm-lock.yaml ./" in dockerfile
    assert "pnpm install --frozen-lockfile" in dockerfile
    assert "site-packages" not in dockerfile
