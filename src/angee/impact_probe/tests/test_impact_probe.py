"""Per-actor visibility of impactprobe rows under the ownership gate.

Runs on the composed host: ``apps.get_model`` reads the runtime model the
composer emitted for ``angee.impact_probe``. Run it with the addon's isolated test
host or a consumer pytest host that enables the addon and loads the base
permission fixtures.
"""

from __future__ import annotations

from django.apps import apps
from django.contrib.auth import get_user_model
from django.test import TestCase
from rebac import system_context

ImpactProbe = apps.get_model("impact_probe", "ImpactProbe")
User = get_user_model()


class ImpactProbeVisibilityTests(TestCase):
    """Drive the composed impactprobe model through the permission schema."""

    def setUp(self) -> None:
        with system_context(reason="impact_probe test setup"):
            self.owner = User.objects.create_user(username="owner", password="owner")
            self.outsider = User.objects.create_user(username="outsider", password="outsider")
            self.row = ImpactProbe.objects.create(title="First", owner=self.owner)

    def test_owner_reads_own_rows_and_an_outsider_reads_none(self) -> None:
        """The field-backed owner relation scopes reads without storing a tuple."""

        self.assertEqual([visible.pk for visible in ImpactProbe.objects.as_user(self.owner)], [self.row.pk])
        self.assertTrue(ImpactProbe.objects.as_user(self.outsider).exists())
