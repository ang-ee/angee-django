import pytest
from django.conf import settings
from django.contrib.auth.models import AnonymousUser
from django.core.exceptions import ImproperlyConfigured, PermissionDenied, ValidationError
from django.test import override_settings

from angee.provenance.engine.checks import check_configuration
from angee.provenance.engine.policy import Policy, get_policy
from tests.provenance_engine.storage.models import Changeset


@pytest.mark.django_db
def test_default_policy_does_not_infer_authority_from_attribution(actor):
    row = Changeset(actor_ref="service:trusted")
    for unknown in (None, "service:trusted", AnonymousUser()):
        with pytest.raises(PermissionDenied):
            get_policy().authorize(unknown, "export", row)
    get_policy().authorize(actor, "export", row)


@pytest.mark.django_db
def test_default_policy_denies_authenticated_user_without_permission(django_user_model):
    user = django_user_model.objects.create_user(username="reader")
    with pytest.raises(PermissionDenied):
        Policy().authorize(user, "change", Changeset())
    assert Policy().source_kind(user) == "person"
    assert Policy().history_user(user) is user


def test_unpersisted_principal_is_not_statement_source(django_user_model):
    with pytest.raises(ValidationError):
        Policy().source_kind(django_user_model(pk=99))


def test_policy_settings_are_lazy_and_validated():
    policy = Policy()
    with override_settings(PROVENANCE_POLICY=policy):
        assert get_policy() is policy
    with override_settings(PROVENANCE_POLICY=object()):
        with pytest.raises(ImproperlyConfigured):
            get_policy()
        assert "provenance.E002" in {error.id for error in check_configuration()}
    with override_settings(PROVENANCE_CHANGESET_MODEL="missing.Model", PROVENANCE_STRICT="true"):
        assert {"provenance.E001", "provenance.E003"} <= {error.id for error in check_configuration()}


def test_missing_swappable_setting_has_actionable_system_check():
    with override_settings(PROVENANCE_CHANGESET_MODEL=None):
        del settings.PROVENANCE_CHANGESET_MODEL
        assert "provenance.E004" in {error.id for error in check_configuration()}


@pytest.mark.django_db
def test_native_source_resolution_proves_persistence_without_granting_authority(actor):
    stored = Policy().resolve_source(actor, using="default")
    assert stored.pk == actor.pk and stored._state.db == "default"
    with pytest.raises(ValidationError, match="persisted Django principal"):
        Policy().resolve_source("external:actor", using="default")
    with pytest.raises(ValidationError, match="subject database"):
        Policy().resolve_source(actor, using="other")


@pytest.mark.django_db
def test_explicit_principal_classification_precedes_auth_user_fallback(monkeypatch):
    from tests.provenance_engine.statements_app.models import Principal

    principal = Principal.objects.create(name="declared auth principal", kind="directory")
    monkeypatch.setattr("angee.provenance.engine.policy.get_user_model", lambda: Principal)
    assert Policy().source_kind(principal) == "directory"
    principal.kind = ""
    with pytest.raises(ValidationError, match="unknown source kind"):
        Policy().source_kind(principal)
