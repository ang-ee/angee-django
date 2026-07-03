"""Regression coverage for the published runtime image contract."""

from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DOCKERFILE = ROOT / "Dockerfile"
ENTRYPOINT = ROOT / "docker" / "runtime-entrypoint.sh"


def test_runtime_image_prepares_bind_mount_outputs_before_dropping_privileges() -> None:
    dockerfile = DOCKERFILE.read_text(encoding="utf-8")
    entrypoint = ENTRYPOINT.read_text(encoding="utf-8")

    assert "gosu" in dockerfile
    assert "COPY docker/runtime-entrypoint.sh /usr/local/bin/angee-django-entrypoint" in dockerfile
    assert 'ENTRYPOINT ["tini", "--", "/usr/local/bin/angee-django-entrypoint"]' in dockerfile
    assert "mkdir -p /app/runtime /app/.angee/data" in entrypoint
    assert "chown -R angee:angee /app/runtime" in entrypoint
    assert "chown angee:angee /app/.angee/data" in entrypoint
    assert "chown -R angee:angee /app/runtime /app/.angee/data" not in entrypoint
    assert "-exec chown -R angee:angee" not in entrypoint
    assert "find /app/.angee/data" not in entrypoint
    assert 'exec gosu angee "$@"' in entrypoint
