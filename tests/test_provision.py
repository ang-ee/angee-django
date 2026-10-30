"""Angee provision hooks compose native commands in one selected process."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path

import pytest
import strawberry
from django.apps import apps
from django.core.management import call_command
from django.core.management.base import CommandError
from django.test import override_settings
from django.test.utils import override_system_checks
from django_compose import provision
from django_compose.management.commands.provision import Command
from rebac import schema_changes
from rebac.backends import reset_backend
from rebac.checks import check_universal_admin_in_roles
from rebac.errors import SchemaError
from rebac.models import SchemaRelation

from angee.graphql.checks import check_graphql_schemas
from angee.graphql.schema import GraphQLSchemas, SchemaParts
from angee.graphql.sdl import SchemaIntegration
from tests.composed_host import COMPOSED_TEST_TIMEOUT


def _environment(tmp_path: Path) -> dict[str, str]:
    source_root = Path(__file__).resolve().parents[1]
    environment = dict(os.environ)
    environment.pop("DJANGO_SETTINGS_MODULE", None)
    environment.pop("DJANGO_COMPOSE_MODE", None)
    environment["PYTHONPATH"] = os.pathsep.join(
        filter(None, (str(tmp_path), str(source_root), environment.get("PYTHONPATH", "")))
    )
    return environment


def test_native_composed_test_runner_does_not_require_provenance(tmp_path: Path) -> None:
    """The source test host can run a plain Django app test without addon boundaries."""

    root = Path(__file__).resolve().parents[1]
    addon = tmp_path / "addons" / "native_probe"
    addon.mkdir(parents=True)
    (addon / "__init__.py").write_text("")
    (addon / "tests.py").write_text(
        'from django.apps import apps\nfrom django.test import SimpleTestCase\n'
        'class CoreTests(SimpleTestCase):\n'
        '    def test_native_host(self):\n'
        '        self.assertTrue(apps.is_installed("django_compose"))\n'
        '        self.assertFalse(apps.is_installed("angee.provenance"))\n'
    )
    report = tmp_path / "report.json"
    result = subprocess.run(
        [sys.executable, str(root / "tests/composed_host.py"), "--runtime-dir", str(tmp_path / "runtime"),
         "--addon-dir", str(addon.parent), "--app", "django.contrib.contenttypes",
         "--app", "django.contrib.auth", "--app", "native_probe",
         "--no-examples", "--action", "tests",
         "--test-label", "native_probe.tests", "--output", str(report)],
        cwd=root, env=_environment(tmp_path), capture_output=True, text=True,
        timeout=COMPOSED_TEST_TIMEOUT, check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert json.loads(report.read_text()) == {"failures": 0, "vendor": "sqlite"}


@pytest.mark.django_db
def test_rebac_sync_invalidates_the_native_backend_cache() -> None:
    """The native sync command discards the process-cached REBAC backend."""

    from rebac.backends import backend

    before = backend()
    call_command("rebac", "sync", "--yes", verbosity=0)
    assert backend() is not before


@pytest.mark.django_db
@schema_changes()
def test_provision_sync_replaces_invalid_historical_subject_sets_before_checks() -> None:
    """Old permission usersets cannot prevent sync from installing their replacement."""

    call_command("rebac", "sync", "--yes", verbosity=0)
    relation = SchemaRelation.objects.get(definition__resource_type="storage/role", name="includes")
    relation.allowed_subjects = [{"type": "storage/role", "relation": "effective_member", "wildcard": False}]
    relation.save(update_fields=["allowed_subjects"])
    reset_backend()

    # Exercise a native schema-reading check without requiring a fully composed
    # concrete model graph in this bare test host.
    with (
        override_settings(REBAC_UNIVERSAL_ADMIN_ROLE="angee/role:admin", ANGEE_REBAC_FORCE_OVERWRITE=True),
        override_system_checks([check_universal_admin_in_roles]),
    ):
        with pytest.raises(SchemaError, match="names a permission"):
            call_command("check", verbosity=0)
        command = Command()
        apps.get_app_config("base").compose_provision(
            provision.ProvisionContext("default", command.stdout, command.stderr),
        )
        provision.run_step(command, ["check"])


@pytest.mark.django_db
def test_graphql_check_then_schema_command_reuses_the_built_schema(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The native check and prebuilt SDL command share discovery's completed schema build."""

    @strawberry.type
    class Query:
        value: str = "ready"

    schemas = GraphQLSchemas([])
    schemas.__dict__["parts"] = {"public": SchemaParts(query=(Query,))}
    original_build = schemas._build
    builds: list[strawberry.Schema] = []

    def counted_build(name: str) -> strawberry.Schema:
        built = original_build(name)
        builds.append(built)
        return built

    monkeypatch.setattr(schemas, "_build", counted_build)
    monkeypatch.setattr(GraphQLSchemas, "from_discovery", classmethod(lambda cls: schemas))
    with override_system_checks([check_graphql_schemas]):
        with override_settings(COMPOSE_OUTPUT_DIR=tmp_path / "runtime"):
            for relative, content in SchemaIntegration().render(None, None).items():
                target = tmp_path / "runtime" / relative
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_bytes(content)
            call_command("check", verbosity=0)
            call_command("schema", check=True, verbosity=0)
            with pytest.raises(CommandError, match="compiler-owned"):
                call_command("schema", verbosity=0)

    assert len(builds) == 1
    assert schemas.build("public") is builds[0]
    assert "value: String!" in (tmp_path / "runtime" / "schemas" / "public.graphql").read_text()
