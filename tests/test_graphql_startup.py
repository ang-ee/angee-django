"""Fresh-process coverage for GraphQL application startup."""

from __future__ import annotations

import os
import subprocess
import sys
import textwrap
from pathlib import Path

import pytest

from angee.base.testing.packages import write_addon_distribution
from tests.composed_host import COMPOSED_TEST_TIMEOUT


@pytest.fixture
def cold_graphql_addon(tmp_path: Path) -> Path:
    """Declare one change feed for both bare and emitted startup hosts."""
    package = tmp_path / "cold_graphql_addon"
    package.mkdir()
    (package / "__init__.py").write_text("", encoding="utf-8")
    (package / "apps.py").write_text(
        textwrap.dedent(
            """
            from django.apps import AppConfig


            class ColdGraphQLAddonConfig(AppConfig):
                name = "cold_graphql_addon"
            """
        ),
        encoding="utf-8",
    )
    (package / "models.py").write_text(
        textwrap.dedent(
            """
            from django.db import models


            class PublishedNote(models.Model):
                title = models.CharField(max_length=80)
            """
        ),
        encoding="utf-8",
    )
    (package / "schema.py").write_text(
        textwrap.dedent(
            """
            import strawberry

            from django.apps import apps
            from angee.graphql.subscriptions import changes


            @strawberry.type
            class Query:
                ready: bool = True


            PublishedNote = apps.get_model("cold_graphql_addon", "PublishedNote")

            schemas = {
                "public": {
                    "query": [Query],
                    "subscription": [
                        changes(PublishedNote, field="publishedNoteChanged")
                    ],
                },
            }
            """
        ),
        encoding="utf-8",
    )
    write_addon_distribution(
        package, "cold_graphql_addon", depends_on=("angee.graphql",), metadata_root=tmp_path,
    )

    return package


def test_graphql_startup_defers_schema_checks(cold_graphql_addon: Path) -> None:
    """Bare Django startup stays lazy and explicit checks report every bad bucket."""

    tmp_path = cold_graphql_addon.parent
    with (cold_graphql_addon / "schema.py").open("a", encoding="utf-8") as schema:
        schema.write('\nschemas.update({name: {"mutation": [Query]} for name in ("broken_alpha", "broken_beta")})\n')

    program = textwrap.dedent(
        """
        import io
        from contextlib import redirect_stdout

        import strawberry
        from django.conf import settings

        builds = []
        original_init = strawberry.Schema.__init__

        def recording_init(self, *args, **kwargs):
            builds.append(type(self).__name__)
            original_init(self, *args, **kwargs)

        strawberry.Schema.__init__ = recording_init
        settings.configure(
            SECRET_KEY="cold-startup-test",
            INSTALLED_APPS=[
                "django.contrib.contenttypes",
                "django.contrib.auth",
                "rebac",
                "angee.graphql.apps.GraphQLConfig",
                "cold_graphql_addon.apps.ColdGraphQLAddonConfig",
            ],
            DATABASES={"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": ":memory:"}},
            DEFAULT_AUTO_FIELD="django.db.models.BigAutoField",
            USE_TZ=True,
        )

        import django
        django.setup()

        from django.apps import apps
        from django.core.management import ManagementUtility, call_command
        from django.core.management.base import SystemCheckError
        from django.core.checks.registry import registry
        from django.db.models.signals import post_delete, post_save
        from cold_graphql_addon.models import PublishedNote
        from angee.graphql.checks import check_graphql_schemas
        from angee.graphql.schema import GraphQLSchemas

        with redirect_stdout(io.StringIO()):
            ManagementUtility(["manage.py", "--help"]).execute()
        assert builds == [], f"startup constructed Strawberry schemas: {builds}"
        assert not post_save.has_listeners(PublishedNote), "bare startup scanned source feeds"
        assert not post_delete.has_listeners(PublishedNote), "bare startup scanned source feeds"

        config = apps.get_app_config("graphql")
        config.ready()
        config.ready()
        assert sum(check is check_graphql_schemas for check in registry.registered_checks) == 1
        assert builds == [], f"check registration constructed Strawberry schemas: {builds}"

        schemas = GraphQLSchemas.from_discovery()
        expected_successes = set(schemas.names()) - {"broken_alpha", "broken_beta"}
        try:
            call_command("check", verbosity=0)
        except SystemCheckError as error:
            report = str(error)
        else:
            raise AssertionError("broken schema passed the default Django check")
        assert report.count("angee.graphql.E002") == 2, report
        assert "GraphQL schema 'broken_alpha' failed to build" in report, report
        assert "GraphQL schema 'broken_beta' failed to build" in report, report

        assert set(schemas._builds) == expected_successes, schemas._builds
        assert builds == ["AngeeSchema"] * len(expected_successes), builds
        schema = schemas.build("public")
        assert builds == ["AngeeSchema"] * len(expected_successes), f"cached schema access rebuilt: {builds}"
        assert schema.get_type_by_name("Query") is not None
        """
    )
    _run_startup(program, tmp_path)


def test_graphql_startup_connects_emitted_publishers_without_building_schema(cold_graphql_addon: Path) -> None:
    """Verified publisher output wires emitted models without constructing a live schema."""

    tmp_path = cold_graphql_addon.parent
    (cold_graphql_addon / "models.py").write_text(
        textwrap.dedent(
            """
            from django.db import models
            from django_compose.models import Composable


            class PublishedNote(Composable, models.Model, concrete=True):
                title = models.CharField(max_length=80)

                class Meta:
                    abstract = True
            """
        ),
        encoding="utf-8",
    )
    program = textwrap.dedent(
        """
        import io
        import json
        import sys
        from contextlib import redirect_stdout
        from pathlib import Path

        import strawberry
        from tests.composed_host import boot

        builds = []
        original_init = strawberry.Schema.__init__

        def recording_init(self, *args, **kwargs):
            builds.append(type(self).__name__)
            original_init(self, *args, **kwargs)

        strawberry.Schema.__init__ = recording_init
        # boot composes in a separate process, then starts this process against
        # that real receipt. Only serving-process schema builds are recorded.
        boot(Path(sys.argv[1]) / "src", Path(sys.argv[2]) / "runtime", [Path(sys.argv[2])],
             root_apps=["cold_graphql_addon"], include_examples=False)

        from django.apps import apps
        from django.core.management import ManagementUtility
        from django.db.models.signals import post_delete, post_save
        from django_compose.serving import artifact_path
        from angee.graphql.schema import GraphQLSchemas
        from angee.graphql.sdl import PUBLISHERS_ARTIFACT

        model = apps.get_model("cold_graphql_addon", "PublishedNote")
        assert model.__module__.startswith("runtime."), model.__module__
        publishers = json.loads(artifact_path(PUBLISHERS_ARTIFACT).read_text())
        assert model._meta.label_lower in publishers, publishers
        assert post_save.has_listeners(model), "emitted save publisher was not connected"
        assert post_delete.has_listeners(model), "emitted delete publisher was not connected"
        with redirect_stdout(io.StringIO()):
            ManagementUtility(["manage.py", "--help"]).execute()
        assert builds == [], f"startup constructed Strawberry schemas: {builds}"

        schema = GraphQLSchemas.from_discovery().build("public")
        assert builds == ["AngeeSchema"], builds
        assert schema.get_type_by_name("Query") is not None
        """
    )
    _run_startup(program, tmp_path, timeout=COMPOSED_TEST_TIMEOUT)


def _run_startup(program: str, tmp_path: Path, *, timeout: int = 30) -> None:
    """Run a cold host with only its fixture addon and the current source on its path."""

    root = Path(__file__).resolve().parents[1]
    env = dict(os.environ)
    env.pop("DJANGO_SETTINGS_MODULE", None)
    python_path = [str(tmp_path), str(root), str(root / "src")]
    if env.get("PYTHONPATH"):
        python_path.append(env["PYTHONPATH"])
    env["PYTHONPATH"] = os.pathsep.join(python_path)
    result = subprocess.run(
        [sys.executable, "-c", program, str(root), str(tmp_path)],
        cwd=root,
        env=env,
        capture_output=True,
        text=True,
        timeout=timeout,
        check=False,
    )

    assert result.returncode == 0, (
        f"fresh GraphQL startup lifecycle failed:\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
