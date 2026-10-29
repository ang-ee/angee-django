"""Anthropic's portable ledger rehome keeps retained target and hash identities."""

from types import SimpleNamespace

import pytest
from django.apps import apps
from django.db import connection, transaction
from rebac import system_context

from angee.agents_integrate_anthropic.runtime_migrations.rehome_anthropic_oauth_resources import rehome
from angee.resources.testing.models import Resource


@pytest.mark.django_db
def test_anthropic_resource_rehome_preserves_ledger_and_target_identity():
    with system_context(reason="test.anthropic.resource_rehome"):
        row = Resource.objects.create(
            source_addon="angee.integrate", source_path="resources/install/010_integrate.oauthclient.yaml",
            tier="install", xref="oauth_anthropic_personal", content_hash="retained-hash",
            target_model="integrate.OAuthClient", target_id="ocl_existing",
        )
        before = {field.attname: getattr(row, field.attname) for field in row._meta.concrete_fields}
        rehome(apps, SimpleNamespace(connection=connection))
        row.refresh_from_db()
        for field, value in before.items():
            assert getattr(row, field) == ("angee.agents_integrate_anthropic" if field == "source_addon" else value)
        rehome(apps, SimpleNamespace(connection=connection))
        assert not Resource.objects.filter(source_addon="angee.integrate", xref=row.xref).exists()


@pytest.mark.django_db
def test_anthropic_resource_rehome_refuses_competing_ledger_identity():
    with system_context(reason="test.anthropic.resource_collision"):
        values = dict(source_path="presets.yaml", tier="install", xref="oauth_anthropic_personal",
                      content_hash="retained", target_model="integrate.OAuthClient", target_id="ocl_existing")
        old = Resource.objects.create(source_addon="angee.integrate", **values)
        Resource.objects.create(source_addon="angee.agents_integrate_anthropic", **values)
        with pytest.raises(RuntimeError, match="Both Integrate and Anthropic"), transaction.atomic():
            rehome(apps, SimpleNamespace(connection=connection))
        assert Resource.objects.filter(pk=old.pk, source_addon="angee.integrate").exists()
