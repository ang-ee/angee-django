"""Regression coverage for the published runtime image contract."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DOCKERFILE = ROOT / "Dockerfile"
ENTRYPOINT = ROOT / "docker" / "runtime-entrypoint.sh"
WEB_ENTRYPOINT = ROOT / "docker" / "web-entrypoint.sh"
PYPROJECT = ROOT / "pyproject.toml"


def test_runtime_image_prepares_bind_mount_outputs_before_dropping_privileges() -> None:
    dockerfile = DOCKERFILE.read_text(encoding="utf-8")
    entrypoint = ENTRYPOINT.read_text(encoding="utf-8")

    assert "gosu" in dockerfile
    assert "COPY docker/runtime-entrypoint.sh /usr/local/bin/angee-django-entrypoint" in dockerfile
    assert 'ENTRYPOINT ["tini", "--", "/usr/local/bin/angee-django-entrypoint"]' in dockerfile
    assert "mkdir -p /app/runtime /app/.angee/data /app/caches/uv" in entrypoint
    assert "chown -R angee:angee /app/runtime" in entrypoint
    assert "chown angee:angee /app/.angee/data" in entrypoint
    assert "chown angee:angee /app/caches/uv" in entrypoint
    assert "chown -R angee:angee /app/runtime /app/.angee/data" not in entrypoint
    assert "-exec chown -R angee:angee" not in entrypoint
    assert "find /app/.angee/data" not in entrypoint
    assert 'exec gosu angee "$@"' in entrypoint


def test_runtime_image_writes_the_bind_mount_as_its_host_owner() -> None:
    """A host-owned mount stays writable both ways: uid 1000 is not the host user."""

    entrypoint = ENTRYPOINT.read_text(encoding="utf-8")

    assert 'app_uid="$(stat -c %u /app)"' in entrypoint
    assert 'usermod -o -u "$app_uid" angee' in entrypoint


def test_dependency_compilers_stay_in_the_build_stage() -> None:
    """Source distributions compile once, and no serving image carries the toolchain."""

    dockerfile = DOCKERFILE.read_text(encoding="utf-8")
    base = dockerfile.split("FROM python:${PYTHON_VERSION}-slim AS base", 1)[1].split("FROM base AS deps", 1)[0]
    deps = dockerfile.split("FROM base AS deps", 1)[1].split("FROM base AS runtime-base", 1)[0]
    runtime_base = dockerfile.split("FROM base AS runtime-base", 1)[1].split("FROM runtime-base AS final", 1)[0]

    assert "build-essential clang cmake" in deps
    assert "build-essential" not in base
    # Both serving targets restart from the compiler-free base and take the venv.
    assert "FROM runtime-base AS final" in dockerfile
    assert "FROM runtime-base AS runtime" in dockerfile
    assert "COPY --from=deps --chown=angee:angee /opt/.venv /opt/.venv" in runtime_base
    # The entrypoint sits after the dependency layer: bootstrap edits must not
    # invalidate it.
    assert "COPY docker/runtime-entrypoint.sh" in runtime_base
    assert "COPY docker/runtime-entrypoint.sh" not in base
    assert "COPY docker/runtime-entrypoint.sh" not in deps
    # Group rights mirror the owner's so the venv survives the entrypoint's uid remap.
    assert "chmod -R g=u /opt/.venv" in deps


def test_python_olm_declares_the_toolchain_its_flag_belongs_to() -> None:
    """The clang-only flag never reaches GCC, and CMake 4 gets its policy floor."""

    pyproject = PYPROJECT.read_text(encoding="utf-8")

    assert 'CC = "clang"' in pyproject
    assert 'CXX = "clang++"' in pyproject
    assert 'CXXFLAGS = "-fdelayed-template-parsing"' in pyproject
    assert 'CMAKE_POLICY_VERSION_MINIMUM = "3.5"' in pyproject


def test_web_image_writes_the_project_mount_as_its_host_owner() -> None:
    """Frontend outputs land host-owned, matching what the django container writes."""

    dockerfile = DOCKERFILE.read_text(encoding="utf-8")
    entrypoint = WEB_ENTRYPOINT.read_text(encoding="utf-8")
    web = dockerfile.split("FROM node:22-slim AS angee-web", 1)[1]

    assert "gosu" in web
    assert "COREPACK_ENABLE_DOWNLOAD_PROMPT=0" in web
    assert "chmod -R g=u /opt/angee-web" in web
    assert "COPY docker/web-entrypoint.sh /usr/local/bin/angee-web-entrypoint" in web
    assert 'ENTRYPOINT ["/usr/local/bin/angee-web-entrypoint"]' in web
    assert 'project_uid="$(stat -c %u /opt/angee-web/project)"' in entrypoint
    assert 'usermod -o -u "$project_uid" node' in entrypoint
    assert "chown -R node:node /opt/angee-web/packages /opt/angee-web/node_modules" in entrypoint
    # Never the mount itself: it is already host-owned.
    assert "chown -R node:node /opt/angee-web/project" not in entrypoint
    assert 'exec gosu node "$@"' in entrypoint
