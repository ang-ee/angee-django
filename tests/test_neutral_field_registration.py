"""Neutral field imports and enabled native GraphQL scalar lifecycle."""

import os
import subprocess
import sys
import textwrap
from pathlib import Path
from unittest.mock import patch

from django.apps import apps
from django.test import SimpleTestCase

from angee.base.testing.packages import write_addon_distribution
from tests.composed_host import run_composed_tests


def test_fields_import_without_graphql_or_strawberry():
    source = Path(__file__).resolve().parents[1] / "src"
    result = subprocess.run(
        [sys.executable, "-c", textwrap.dedent("""
            import importlib.abc
            import sys
            from django.conf import settings
            settings.configure(USE_I18N=False)

            class UnavailableGraphQL(importlib.abc.MetaPathFinder):
                def find_spec(self, fullname, path=None, target=None):
                    if (
                        fullname == "angee.graphql"
                        or fullname.startswith("angee.graphql.")
                        or fullname.split(".")[0] in {"strawberry", "strawberry_django"}
                    ):
                        raise ModuleNotFoundError(fullname, name=fullname)

            sys.meta_path.insert(0, UnavailableGraphQL())
            from angee.money.fields import MoneyField
            from angee.scheduling.fields import RecurrenceField
            from angee.parties.fields import CountryCodeField
            assert MoneyField().decimal_places == 6
            assert RecurrenceField().clean("FREQ=DAILY;COUNT=2", None) == "FREQ=DAILY;COUNT=2"
            assert CountryCodeField().to_python("United States") == "US"
            assert not any(name.startswith(("angee.graphql", "strawberry")) for name in sys.modules)
        """)],
        env={**os.environ, "PYTHONPATH": str(source)},
        capture_output=True,
        text=True,
        check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr


class EnabledScalarContract(SimpleTestCase):
    __test__ = False

    def test_native_schema_is_ready_after_earlier_graphql_app(self):
        from decimal import Decimal

        from django.core.exceptions import ImproperlyConfigured
        from strawberry_django.fields.types import field_type_map

        from angee.graphql.field_types import register_field_type
        from angee.graphql.schema import GraphQLSchemas
        from angee.money.fields import MoneyField
        from angee.parties.fields import CountryCodeField
        from angee.scheduling.fields import RecurrenceField

        names = [config.name for config in apps.get_app_configs()]
        for owner in ("angee.money", "angee.scheduling", "angee.parties"):
            self.assertLess(names.index("angee.graphql"), names.index(owner))
        sdl = GraphQLSchemas.from_discovery().render_sdl()["public"]
        self.assertIn("amount: Decimal!", sdl)
        self.assertIn("recurrence: String!", sdl)
        self.assertIn("country: String!", sdl)
        owners = ("angee.money", "angee.scheduling", "angee.parties")
        for config in apps.get_app_configs():
            if config.name in owners:
                config.ready()
        self.assertIs(field_type_map[MoneyField], Decimal)
        self.assertIs(field_type_map[RecurrenceField], str)
        self.assertIs(field_type_map[CountryCodeField], str)
        with self.assertRaises(ImproperlyConfigured):
            register_field_type(MoneyField, str)
        self.assertIs(field_type_map[MoneyField], Decimal)
        self.assertEqual(GraphQLSchemas.from_discovery().render_sdl()["public"], sdl)

    def test_registration_uses_the_owning_isolated_registry(self):
        from django.apps.registry import Apps
        from strawberry_django.fields.types import field_type_map

        from angee.money.fields import MoneyField
        from angee.parties.fields import CountryCodeField
        from angee.scheduling.fields import RecurrenceField

        self.assertTrue(apps.is_installed("angee.graphql"))
        fields = (MoneyField, RecurrenceField, CountryCodeField)
        # Global GraphQL remains enabled; this real registry has only field owners.
        with patch.dict(field_type_map):
            for field in fields:
                del field_type_map[field]
            registry = Apps(installed_apps=("angee.money", "angee.scheduling", "angee.parties"))
            self.assertFalse(registry.is_installed("angee.graphql"))
            for config in registry.get_app_configs():
                self.assertIs(config.apps, registry)
            for field in fields:
                self.assertNotIn(field, field_type_map)
        for field in fields:
            self.assertIn(field, field_type_map)


def test_enabled_native_scalar_schema(tmp_path):
    addon = tmp_path / "addons" / "example" / "scalar_fields"
    addon.mkdir(parents=True)
    (addon / "__init__.py").touch()
    write_addon_distribution(
        addon, "example.scalar_fields",
        depends_on=("angee.money", "angee.scheduling", "angee.parties", "angee.graphql"),
    )
    (addon / "models.py").write_text(textwrap.dedent("""
        from django.db import models
        from angee.money.fields import MoneyField
        from angee.scheduling.fields import RecurrenceField
        from angee.parties.fields import CountryCodeField

        class ScalarRecord(models.Model):
            currency = models.ForeignKey("money.Currency", on_delete=models.PROTECT)
            amount = MoneyField()
            recurrence = RecurrenceField()
            country = CountryCodeField()
    """))
    (addon / "schema.py").write_text(textwrap.dedent("""
        import strawberry
        import strawberry_django
        from django.apps import apps
        from strawberry import auto

        @strawberry_django.type(apps.get_model("scalar_fields", "ScalarRecord"))
        class ScalarRecord:
            amount: auto
            recurrence: auto
            country: auto

        @strawberry.type
        class Query:
            scalar_record: ScalarRecord | None = None

        schemas = {"public": {"query": [Query]}}
    """))
    run_composed_tests(
        tmp_path, "tests.test_neutral_field_registration.EnabledScalarContract",
        app="example.scalar_fields", addon_dirs=(tmp_path / "addons",),
    )
