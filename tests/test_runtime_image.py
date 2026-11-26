"""Regression coverage for the published runtime image contract."""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DOCKERFILE = ROOT / "Dockerfile"
ENTRYPOINT = ROOT / "docker" / "runtime-entrypoint.sh"


def test_image_python_is_the_release_python_version_pins() -> None:
    """Every Python stage builds on the CPython release `.python-version` pins.

    The dependency stage's `uv sync` reads that file and asks for exactly its release,
    and the image never downloads an interpreter, so a base tag on any other release
    fails the build. A Dockerfile cannot read a context file into `FROM`, so the
    `PYTHON_VERSION` default follows the pin and a pin change edits both.
    """

    dockerfile = DOCKERFILE.read_text(encoding="utf-8")
    (default,) = re.findall(r"^ARG PYTHON_VERSION=(.*)$", dockerfile, re.MULTILINE)
    assert default == (ROOT / ".python-version").read_text(encoding="utf-8").strip()
    assert set(re.findall(r"^FROM python:(\S+)", dockerfile, re.MULTILINE)) == {"${PYTHON_VERSION}-slim"}


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


def test_runtime_image_runs_as_the_owner_of_the_mounted_project() -> None:
    """A Linux bind mount keeps numeric ids: the image's angee user takes the project owner's uid and gid.

    The stack root belongs to whoever rendered it, whatever its uid, as the operator
    image already assumes; a root-owned project keeps the image's own user.
    """

    entrypoint = ENTRYPOINT.read_text(encoding="utf-8")
    assert 'uid=$(stat -c %u "$project_dir")' in entrypoint
    assert 'gid=$(stat -c %g "$project_dir")' in entrypoint
    assert 'if [ "$uid" != "0" ] && [ "$uid" != "$(id -u angee)" ]; then' in entrypoint
    assert 'groupmod -o -g "$gid" angee' in entrypoint
    assert 'usermod -o -u "$uid" angee' in entrypoint
    owned_by_project = entrypoint.index('usermod -o -u "$uid" angee')
    assert owned_by_project < entrypoint.index('chown -R angee:angee "$project_dir/runtime"')
    assert ".angee" not in entrypoint


def test_baked_image_installs_native_distributions_without_path_injection() -> None:
    """The baked image installs workspace packages and their locked public dependencies."""

    dockerfile = DOCKERFILE.read_text()
    assert "COPY --chown=angee:angee src ./src" in dockerfile
    assert "COPY --chown=angee:angee examples/src ./examples/src" in dockerfile
    assert "COPY --chown=angee:angee tests/blankchoices ./tests/blankchoices" in dockerfile
    assert "from=django-compose" not in dockerfile
    assert "uv sync --frozen --no-dev --no-editable --extra postgres" in dockerfile
    assert "addon-settings.yaml" not in dockerfile
    assert "ANGEE_ADDON_DIRS" not in dockerfile
    assert "YAMLCONF_CONFFILE" not in dockerfile


def test_web_image_uses_native_package_sources_and_build_context() -> None:
    """The web image consumes source package manifests instead of wheel contents."""

    dockerfile = DOCKERFILE.read_text()
    ignored = (ROOT / ".dockerignore").read_text().splitlines()
    assert "packages" not in ignored
    assert "COPY packages ./packages" in dockerfile
    assert "COPY src ./src" in dockerfile
    assert "COPY package.json pnpm-workspace.yaml pnpm-lock.yaml ./" in dockerfile
    assert "pnpm install --frozen-lockfile" in dockerfile
    assert "site-packages" not in dockerfile
