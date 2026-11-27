"""The PostgreSQL lane's sessions run with JIT off, as a deployment's do."""

import json
import os
import subprocess
import sys
from pathlib import Path
from urllib.parse import urlencode, urlsplit

import pytest
from django.db import connection

pytestmark = pytest.mark.skipif(connection.vendor != "postgresql", reason="PostgreSQL sessions required")

ROOT = Path(__file__).resolve().parents[1]

_SESSION = """
import json
import django
django.setup()
from django.db import connection
with connection.cursor() as cursor:
    cursor.execute("SHOW jit")
    jit = cursor.fetchone()[0]
    cursor.execute("SHOW lock_timeout")
    lock_timeout = cursor.fetchone()[0]
print(json.dumps({"vendor": connection.vendor, "jit": jit, "lock_timeout": lock_timeout}))
"""


def test_the_lane_session_runs_without_jit(db: None) -> None:
    """The lane's own bare URL reaches pytest-django's test database through ``tests.settings``."""
    with connection.cursor() as cursor:
        cursor.execute("SHOW jit")
        assert (connection.vendor, cursor.fetchone()) == ("postgresql", ("off",))


def test_a_url_that_carries_options_keeps_them_and_runs_without_jit(db: None) -> None:
    """``tests.settings`` keeps a URL's own libpq options and still turns JIT off."""
    url = urlsplit(os.environ["DATABASE_URL"])
    query = "&".join(filter(None, (url.query, urlencode({"options": "-c lock_timeout=1234"}))))
    database_url = url._replace(path=f"/{connection.settings_dict['NAME']}", query=query).geturl()
    result = subprocess.run(
        [sys.executable, "-c", _SESSION],
        cwd=ROOT,
        env={**os.environ, "DATABASE_URL": database_url, "DJANGO_SETTINGS_MODULE": "tests.settings"},
        text=True, capture_output=True, timeout=120, check=False,
    )
    assert result.returncode == 0, result.stderr
    session = json.loads(result.stdout.splitlines()[-1])
    assert session == {"vendor": "postgresql", "jit": "off", "lock_timeout": "1234ms"}
