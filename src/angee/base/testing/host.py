"""Compose a test host for an addon's dependency closure and run the addon's own tests in it.

An addon whose tests live in its ``tests`` package runs them on a host that installs
only that addon, its ``testing`` app when it ships one, and what their native
requirements enable (``django_compose.components.enabled_apps``). The composer emits
the concrete models, so the tests read them from the app registry; nothing copies
composition by hand. The host is written under ``.test-hosts/<addon>/`` and composed
with ``--if-stale``, so a host whose composition inputs have not changed since its last
run starts without composing again::

    uv run --locked python -m angee.base.testing.host angee.uom [pytest arguments]

The run uses SQLite unless ``DATABASE_URL`` names a PostgreSQL server, which then gets a
test database of the addon's own. pytest loads the ``testing.fixtures`` plugin of every
enabled app that ships one, and no conftest above the addon's package.
"""

from __future__ import annotations

import argparse
import importlib.util
import os
import sqlite3
import subprocess
import sys
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

from django.core.exceptions import ImproperlyConfigured

SETTINGS_MODULE = "composed_test_settings"
# Named for the host alone: a project's own ``runtime`` package elsewhere on the path must not shadow it.
OUTPUT_MODULE = "composed_test_runtime"
# The composer and Django read these; a host passes its own and nothing inherited.
SCRUBBED_PREFIXES = ("ANGEE_", "YAMLCONF_")
SCRUBBED_NAMES = frozenset({"DJANGO_SETTINGS_MODULE", "DJANGO_COMPOSE_MODE", "DATABASE_URL", "SENTRY_DSN"})
# What every test host sets: tests run with in-process channels and one reference currency.
TEST_HOST_SETTINGS: Mapping[str, Any] = {
    "ANGEE_MONEY_REFERENCE_CURRENCY": "USD",
    "ANGEE_GRAPHQL_ALLOW_INMEMORY_CHANNEL_LAYER": True,
}


def sqlite_database(path: Path) -> dict[str, Any]:
    """Return a file-backed SQLite test database, refusing a release the suite cannot run on.

    A file, not ``:memory:``, gives each thread its own connection, so a worker thread's
    write waits on the busy timeout instead of failing on a shared connection's table
    lock; WAL keeps concurrent reads non-blocking. SQLite 3.53 is required: older
    releases reject an ``ORDER BY`` naming an outer query's column, and 3.54 refuses
    django-zed-rebac's permission reads (docs/checks.md#sqlite-version).
    """

    if not (3, 53) <= sqlite3.sqlite_version_info < (3, 54):
        raise ImproperlyConfigured(
            f"SQLite {sqlite3.sqlite_version} cannot run the SQLite suite, which needs SQLite 3.53: "
            "older releases reject an ORDER BY naming an outer query's column, and 3.54 refuses "
            "django-zed-rebac's permission reads (VIEWs and/or subqueries nested too deep). "
            "Run it on the uv-managed CPython that .python-version names: uv run --managed-python."
        )
    name = str(path)
    return {
        "ENGINE": "django.db.backends.sqlite3",
        "NAME": name,
        "OPTIONS": {"timeout": 30, "init_command": "PRAGMA journal_mode=WAL;"},
        "TEST": {"NAME": name},
    }


def scrubbed(environ: Mapping[str, str]) -> dict[str, str]:
    """Return ``environ`` without the settings a host must not inherit."""

    return {
        name: value for name, value in environ.items()
        if not name.startswith(SCRUBBED_PREFIXES) and name not in SCRUBBED_NAMES
    }


def write(
    host_root: Path, installed_apps: Sequence[str], database: Mapping[str, Any], *, output_dir: Path | None = None,
) -> None:
    """Write a host's settings: ``angee.base.conf.configure`` over a ``settings.yaml``.

    The composer emits into ``output_dir``, a package the host root imports
    (``<host_root>/composed_test_runtime`` by default).
    """

    import yaml

    output_dir = output_dir or host_root / OUTPUT_MODULE
    host_root.mkdir(parents=True, exist_ok=True)
    (host_root / f"{SETTINGS_MODULE}.py").write_text(
        "from angee.base.conf import configure\nconfigure(globals())\n", encoding="utf-8",
    )
    (host_root / "settings.yaml").write_text(yaml.safe_dump({
        "SECRET_KEY": "isolated-composition-check",
        "COMPOSE_OUTPUT_DIR": str(output_dir),
        "COMPOSE_OUTPUT_MODULE": output_dir.name,
        "INSTALLED_APPS": list(installed_apps),
        "DATABASES": {"default": dict(database)},
        **TEST_HOST_SETTINGS,
    }, sort_keys=True), encoding="utf-8")


def compose(host_root: Path, environ: Mapping[str, str], *, if_stale: bool, timeout: int) -> None:
    """Run the composer for a written host, in a process of its own."""

    result = subprocess.run(
        [sys.executable, "-m", "django_compose", "compose", *(["--if-stale"] if if_stale else [])],
        cwd=host_root, env=dict(environ), capture_output=True, text=True, check=False, timeout=timeout,
    )
    if result.returncode:
        raise RuntimeError(f"Native compose failed:\n{result.stdout}\n{result.stderr}")


def host_environ(host_root: Path, environ: Mapping[str, str]) -> dict[str, str]:
    """Return the environment a host's composer and test process run in."""

    host = scrubbed(environ)
    host["PYTHONPATH"] = os.pathsep.join(filter(None, (str(host_root), environ.get("PYTHONPATH"))))
    host["DJANGO_SETTINGS_MODULE"] = SETTINGS_MODULE
    return host


def run(addon: str, pytest_args: Sequence[str], *, hosts_dir: Path, timeout: int = 1500) -> int:
    """Compose ``addon``'s test host and run its ``tests`` package there; return pytest's exit code."""

    from django_compose.components import enabled_apps

    spec = importlib.util.find_spec(addon)
    if spec is None or not spec.submodule_search_locations:
        raise ImproperlyConfigured(f"{addon} is not an importable package")
    package = Path(next(iter(spec.submodule_search_locations)))
    if not (package / "tests").is_dir():
        raise ImproperlyConfigured(f"{addon} has no tests package")
    # Paths, not imports: a testing package may import models, and Django is not set up here.
    roots = [addon, *([f"{addon}.testing"] if (package / "testing" / "apps.py").is_file() else [])]
    host_root = (hosts_dir / addon).resolve()
    if database_url := os.environ.get("DATABASE_URL"):
        import environ as django_environ

        database = django_environ.Env.db_url_config(database_url)
        if database["ENGINE"] != "django.db.backends.postgresql":
            raise ImproperlyConfigured("A test host takes a PostgreSQL DATABASE_URL or none.")
        database["TEST"] = {"NAME": f"test_{addon.replace('.', '_')}"}
    else:
        database = sqlite_database(host_root / "test.sqlite3")
    write(host_root, roots, database)
    environ = host_environ(host_root, os.environ)
    compose(host_root, environ, if_stale=True, timeout=timeout)
    plugins = [
        f"{entry.name}.testing.fixtures"
        for entry in enabled_apps(roots, group="angee.addons").app_entries
        if (Path(entry.path) / "testing" / "fixtures.py").is_file()
    ]
    return subprocess.run([
        # The host owns the import path: pytest-django must not add an enclosing project's.
        sys.executable, "-m", "pytest", "--ds", SETTINGS_MODULE, "-o", "django_find_project=false",
        "--confcutdir", str(package),
        "--nomigrations", "-p", "django", *(argument for plugin in plugins for argument in ("-p", plugin)),
        str(package / "tests"), *pytest_args,
    ], env=environ, check=False, timeout=timeout).returncode


def main(argv: Sequence[str] | None = None) -> int:
    """Run one addon's tests on its composed host."""

    # Every argument it does not define goes to pytest, so it matches none by prefix.
    parser = argparse.ArgumentParser(
        prog="python -m angee.base.testing.host", description=__doc__.splitlines()[0], allow_abbrev=False,
    )
    parser.add_argument("addon", help="the addon's app name, such as angee.uom")
    parser.add_argument("--hosts-dir", type=Path, default=Path(".test-hosts"), help="where hosts are written")
    options, pytest_args = parser.parse_known_args(argv)
    return run(options.addon, pytest_args, hosts_dir=options.hosts_dir)


if __name__ == "__main__":
    raise SystemExit(main())
