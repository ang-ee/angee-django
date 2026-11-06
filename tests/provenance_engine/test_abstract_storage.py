"""A host can compose abstract storage without registering the default app."""

import os
import subprocess
import sys
import textwrap


def test_abstract_import_does_not_register_concrete_storage():
    environment = os.environ.copy()
    environment.pop("DJANGO_SETTINGS_MODULE", None)
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            textwrap.dedent("""
            import sys
            import django
            from django.apps import apps
            from django.conf import settings

            settings.configure(
                SECRET_KEY="abstract-host-test",
                INSTALLED_APPS=["django.contrib.auth", "django.contrib.contenttypes"],
                DATABASES={"default": {"ENGINE": "django.db.backends.sqlite3", "NAME": ":memory:"}},
            )
            django.setup()
            from angee.provenance.engine.base import AbstractChangeset

            assert AbstractChangeset._meta.abstract
            assert "tests.provenance_engine.storage.models" not in sys.modules
            assert not apps.all_models.get("provenance")

            class HostChangeset(AbstractChangeset):
                class Meta(AbstractChangeset.Meta):
                    app_label = "host_storage"
                    db_table = "historical_host_changeset"

            assert HostChangeset._meta.label == "host_storage.HostChangeset"
            assert HostChangeset._meta.db_table == "historical_host_changeset"
            assert "tests.provenance_engine.storage.models" not in sys.modules
        """),
        ],
        env=environment,
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
