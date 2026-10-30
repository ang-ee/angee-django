"""Native migration authoring reads permission declarations before provisioning."""

from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path


def test_tags_initial_migration_can_be_authored_without_rebac_tables(tmp_path: Path) -> None:
    """Model checks consume the compiled schema while the database is empty."""

    source = Path(__file__).resolve().parents[1]
    (tmp_path / "settings.yaml").write_text(
        "SECRET_KEY: permission-authoring-test\n"
        "INSTALLED_APPS: ['angee.tags']\n"
        f"COMPOSE_OUTPUT_DIR: {str(tmp_path / 'runtime')!r}\n"
        "COMPOSE_OUTPUT_MODULE: runtime\n"
        "DATABASES:\n  default:\n    ENGINE: django.db.backends.sqlite3\n    NAME: ':memory:'\n",
    )
    template = source / "templates/projects/web/template"
    (tmp_path / "settings.py").write_text((template / "settings.py").read_text())
    (tmp_path / "manage.py").write_text(
        (template / "manage.py.jinja").read_text().replace("{{ project_name }}", "permission-authoring"),
    )
    environment = {
        key: value
        for key, value in os.environ.items()
        if not key.startswith(("ANGEE_", "COMPOSE_", "YAMLCONF_", "SENTRY_"))
        and key not in {"DJANGO_SETTINGS_MODULE", "DJANGO_COMPOSE_MODE", "DATABASE_URL", "PYTHONPATH"}
    }
    for arguments in (("compose",), ("makemigrations", "tags", "--noinput")):
        result = subprocess.run(
            [sys.executable, str(tmp_path / "manage.py"), *arguments],
            cwd=tmp_path,
            env=environment,
            capture_output=True,
            text=True,
            timeout=60,
            check=False,
        )
        assert result.returncode == 0, result.stdout + result.stderr
    authored = tuple((tmp_path / "runtime/tags/migrations").glob("0*.py"))
    assert authored, result.stdout + result.stderr
    assert any("name='TagAssignment'" in path.read_text() for path in authored)
