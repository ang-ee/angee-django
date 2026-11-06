"""Permission session emission against generated app models."""

from __future__ import annotations

from pathlib import Path
from tempfile import TemporaryDirectory

from django.apps import apps
from django.core.management import call_command
from django.test import TestCase
from rebac.models import SchemaDefinition

from angee.iam.build import merged_schema_relpath
from angee.iam.testing.permissions import emit_permissions, installed_field_owners


class ComposedPermissionEmissionTests(TestCase):
    def test_spaces_channel_gate_uses_its_source_donor(self) -> None:
        configs = tuple(apps.get_app_configs())
        spaces = apps.get_app_config("spaces")
        assert spaces.models_module is not None
        self.assertTrue(spaces.models_module.__name__.startswith("runtime."))
        self.assertFalse(hasattr(spaces.models_module, "ChannelSpace"))
        self.assertEqual(installed_field_owners(configs)["messaging/channel"]["team"], "angee.spaces")

        with TemporaryDirectory() as directory:
            runtime = Path(directory)
            emit_permissions(configs, runtime)
            self.assertIn(
                "permission write__team =",
                (runtime / merged_schema_relpath("angee.messaging")).read_text(),
            )
            call_command("rebac", "sync", verbosity=0)
            self.assertTrue(
                SchemaDefinition.objects.get(resource_type="messaging/channel")
                .permissions.filter(name="write__team").exists()
            )
