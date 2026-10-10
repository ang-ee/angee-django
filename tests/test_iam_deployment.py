"""Bootstrap recovery and explicit installation ownership use IAM and ledger owners."""

from io import StringIO

import pytest
from django.apps import apps
from django.core.management import call_command
from rebac import system_context

from angee.iam.deployment import bind_deployment_operator, resolve_deployment_operator
from angee.resources.exceptions import ResourceLoadError
from angee.resources.testing.models import Resource
from tests.conftest import create_platform_admin, create_user

pytestmark = pytest.mark.usefixtures("composed_tables")


def test_bootstrap_binds_only_when_unbound_and_keeps_a_different_recovery_admin():
    first = create_platform_admin("original-operator")
    assert bind_deployment_operator(first).pk == first.pk
    output = StringIO()
    call_command("bootstrap_admin", username="rescue-admin", email="rescue@example.com", password="fake", stdout=output)
    with system_context(reason="test recovery administrator survived"):
        user_model = type(first)
        assert user_model.objects.get(username="rescue-admin").is_active
    assert "Existing deployment operator retained" in output.getvalue()
    assert resolve_deployment_operator(ledger_model=Resource).pk == first.pk


def test_operator_resolution_never_adopts_an_admin_and_explicit_rebind_has_a_command():
    first, replacement = create_platform_admin("operator-one"), create_platform_admin("operator-two")
    with pytest.raises(ResourceLoadError, match="unbound"):
        resolve_deployment_operator(ledger_model=Resource)
    bind_deployment_operator(first)
    call_command("rebind_deployment_operator", username="operator-two", stdout=StringIO())
    assert resolve_deployment_operator(ledger_model=Resource).pk == replacement.pk
    assert Resource.objects.filter(xref="deployment_operator").count() == 1


def test_installation_identity_is_immutable_without_the_explicit_rebind_verb():
    first, second = create_platform_admin("bound-first"), create_platform_admin("bound-second")
    with system_context(reason="test stable installation identity"):
        Resource.objects.bind_instance(
            addon=apps.get_app_config("iam"), xref="deployment_operator", instance=first, source="test",
        )
        with pytest.raises(ResourceLoadError, match="another record"):
            Resource.objects.bind_instance(
                addon=apps.get_app_config("iam"), xref="deployment_operator", instance=second, source="test",
            )
    assert resolve_deployment_operator(ledger_model=Resource).pk == first.pk


def test_bootstrap_binding_rejects_a_non_admin_human():
    with system_context(reason="test operator authority"):
        person = create_user("non-admin-operator")
    with pytest.raises(ResourceLoadError, match="platform-admin"):
        bind_deployment_operator(person)
