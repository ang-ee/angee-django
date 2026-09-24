"""Fresh-process coverage for GraphQL application startup."""

from __future__ import annotations

import os
import subprocess
import sys
import textwrap
from pathlib import Path


def test_graphql_startup_discovers_publishers_without_building_schema(tmp_path: Path) -> None:
    """Django startup wires changes while explicit schema access remains lazy."""

    root = Path(__file__).resolve().parents[1]
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

            from .models import PublishedNote
            from angee.graphql.subscriptions import changes


            @strawberry.type
            class Query:
                ready: bool = True


            schemas = {
                "broken_alpha": {
                    "mutation": [Query],
                },
                "broken_beta": {
                    "mutation": [Query],
                },
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
    (package / "addon.toml").write_text(
        textwrap.dedent(
            """
            [addon]
            name = "cold_graphql_addon"
            schemas = "schema.schemas"
            """
        ),
        encoding="utf-8",
    )

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
        assert post_save.has_listeners(PublishedNote), "save publisher was not connected"
        assert post_delete.has_listeners(PublishedNote), "delete publisher was not connected"

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
    env = dict(os.environ)
    env.pop("DJANGO_SETTINGS_MODULE", None)
    python_path = [str(tmp_path), str(root), str(root / "addons")]
    if env.get("PYTHONPATH"):
        python_path.append(env["PYTHONPATH"])
    env["PYTHONPATH"] = os.pathsep.join(python_path)
    result = subprocess.run(
        [sys.executable, "-c", program],
        cwd=root,
        env=env,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )

    assert result.returncode == 0, (
        f"fresh GraphQL startup lifecycle failed:\nstdout:\n{result.stdout}\nstderr:\n{result.stderr}"
    )
