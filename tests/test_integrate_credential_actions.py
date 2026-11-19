"""Manual credential metadata and secret changes retain their existing authorization."""

from datetime import timedelta

import pytest
from django.contrib.auth import get_user_model
from django.db import connection
from django.utils import timezone
from rebac import PermissionDenied, actor_context, system_context

from angee.graphql.testing.documents import execute_schema, result_data
from angee.iam.permissions import PlatformAdminPermission
from tests.conftest import Credential, create_platform_admin, make_integration
from tests.test_iam_graphql import _schema
from tests.test_integrate_jwt_bearer import neutral_key_file

pytestmark = pytest.mark.django_db
UPDATE = """mutation($id: String!, $set: credentials_set_input!) {
  update_credentials_by_pk(pk_columns: {id: $id}, _set: $set) {
    id name expires_at editable_fields replacement_fields
  }
}"""
REPLACE = """mutation($id: ID!, $data: CredentialMaterialInput!, $confirmed: Boolean!) {
  replace_credential_secret(id: $id, data: $data, confirmed: $confirmed) { ok message validation_errors }
}"""
MATERIAL = [
    ("static_token", {"api_key": "old-token"}, {"api_key": "new-token"}),
    ("basic_auth", {"username": "old-user", "password": "old-pass"},
     {"username": "new-user", "password": "new-pass"}),
    ("app_keys", {"app_id": "old-app", "app_secret": "old-secret"},
     {"app_id": "new-app", "app_secret": "new-secret"}),
    ("ssh_key", {"private_key": "old-private-key"}, {"private_key": "new-private-key"}),
    ("jwt_bearer", {"key_file": neutral_key_file(issuer="old-issuer")},
     {"key_file": neutral_key_file(issuer="new-issuer")}),
]


@pytest.mark.parametrize(("kind", "original", "replacement"), MATERIAL)
def test_local_metadata_updates_and_secret_replacement_preserve_bindings(kind, original, replacement, caplog):
    integration = make_integration(f"editable-{kind}", kind=kind, material=original)
    credential = integration.credential
    admin = create_platform_admin(f"credential-{kind}-admin", password=None)
    schema = _schema("console")
    expiry = timezone.now() + timedelta(days=30)
    with system_context(reason="test.credential.cached_material"):
        credential.update_material(
            stale_secret="superseded-secret", scoped_tokens={"scope": {"access_token": "superseded-token"}},
        )
        credential.expires_at = expiry
        credential.save(update_fields=["expires_at"])
    before_id, before_user = credential.pk, credential.user_id
    update = result_data(execute_schema(schema, UPDATE, {
        "id": credential.public_id, "set": {"name": "Renamed key"},
    }, user=admin))["update_credentials_by_pk"]
    assert update["name"] == "Renamed key" and update["expires_at"] is not None
    stored = Credential._base_manager.get(pk=credential.pk)
    assert stored.expires_at == expiry
    assert update["editable_fields"] == stored.editable_fields == ["name"]
    assert update["replacement_fields"] == stored.replacement_fields == list(replacement)

    variables = {"id": credential.public_id, "data": replacement, "confirmed": True}
    refused = result_data(execute_schema(schema, REPLACE, {**variables, "confirmed": False}, user=admin))
    assert not refused["replace_credential_secret"]["ok"]
    assert "confirmed" in refused["replace_credential_secret"]["validation_errors"]
    changed = result_data(execute_schema(schema, REPLACE, variables, user=admin))["replace_credential_secret"]
    assert changed["ok"]
    stored = Credential._base_manager.get(pk=credential.pk)
    assert stored.reveal() == stored.handler.clean(replacement)
    assert (stored.pk, stored.user_id, stored.kind, stored.name, stored.expires_at) == (
        before_id, before_user, kind, "Renamed key", expiry,
    )
    integration.refresh_from_db()
    assert integration.credential_id == before_id
    with connection.cursor() as cursor:
        cursor.execute(f'SELECT material FROM "{Credential._meta.db_table}" WHERE id = %s', [before_id])
        ciphertext = cursor.fetchone()[0]
    for value in replacement.values():
        assert value not in ciphertext + caplog.text + str(changed)
    assert "material" not in schema.as_str().split("type CredentialType", 1)[1].split("}", 1)[0]


def test_metadata_write_remains_owner_or_admin_but_replacement_is_refused_by_zed(monkeypatch):
    owner = get_user_model().objects.create_user("credential-owner")
    with system_context(reason="test.local.credential"):
        credential = Credential.objects.create_local_credential(
            owner, kind="static_token", name="Local", material={"api_key": "x"},
        )
    stranger = get_user_model().objects.create_user("credential-stranger")
    schema = _schema("console")
    variables = {"id": credential.public_id, "set": {"name": "Owner's label"}}
    own = result_data(execute_schema(schema, UPDATE, variables, user=owner))["update_credentials_by_pk"]
    assert own["replacement_fields"] == []
    assert execute_schema(schema, UPDATE, variables, user=stranger).errors

    def unexpected(*args, **kwargs):
        pytest.fail("Replacement must use Zed, not a resolver permission class")

    monkeypatch.setattr(PlatformAdminPermission, "has_permission", unexpected)
    assert credential.with_actor(owner).has_access("write")
    for actor in (owner, stranger):
        assert not credential.with_actor(actor).has_access("replace_secret")
        assert not Credential.objects.with_actor(actor).with_action("replace_secret").filter(pk=credential.pk).exists()
        result = result_data(execute_schema(schema, REPLACE, {
            "id": credential.public_id, "data": {"api_key": "refused-token"}, "confirmed": True,
        }, user=actor))["replace_credential_secret"]
        assert result["ok"] is False and "__all__" in result["validation_errors"]
        with actor_context(actor), pytest.raises(PermissionDenied):
            credential.with_actor(actor).replace_secret({"api_key": "refused-token"}, confirmed=True)
    assert Credential._base_manager.get(pk=credential.pk).reveal() == {"api_key": "x"}


def test_oauth_metadata_and_manual_replacement_are_refused():
    credential = make_integration("oauth-unchanged", kind="oauth", material={"access_token": "oauth-token"}).credential
    admin = create_platform_admin("oauth-edit-admin", password=None)
    owner = get_user_model().objects.create_user("oauth-owner")
    with system_context(reason="test.oauth.owner"):
        credential.user = owner
        credential.save(update_fields=["user"])
    schema = _schema("console")
    for actor in (admin, owner):
        assert credential.with_actor(actor).has_access("write")
        result = execute_schema(schema, UPDATE, {"id": credential.public_id, "set": {"name": "Manual"}}, user=actor)
        assert result.errors and "name" in result.errors[0].extensions["validationErrors"]
        assert Credential._base_manager.get(pk=credential.pk).name == credential.name
    replaced = result_data(execute_schema(schema, REPLACE, {
        "id": credential.public_id, "data": {"api_key": "manual"}, "confirmed": True,
    }, user=admin))["replace_credential_secret"]
    assert not replaced["ok"] and "connection flow" in replaced["message"]
    stored = Credential._base_manager.get(pk=credential.pk)
    assert stored.reveal() == {"access_token": "oauth-token"}
    assert stored.editable_fields == stored.replacement_fields == []
    update = result_data(execute_schema(schema, UPDATE, {
        "id": credential.public_id, "set": {"status": "revoked"},
    }, user=admin))["update_credentials_by_pk"]
    assert update["editable_fields"] == update["replacement_fields"] == []


@pytest.mark.parametrize(("kind", "original", "replacement"), MATERIAL)
def test_replacement_uses_the_kind_handlers_field_validation(kind, original, replacement):
    credential = make_integration(f"invalid-{kind}", kind=kind, material=original).credential
    admin = create_platform_admin(f"invalid-{kind}-admin", password=None)
    result = result_data(execute_schema(_schema("console"), REPLACE, {
        "id": credential.public_id, "data": {}, "confirmed": True,
    }, user=admin))["replace_credential_secret"]
    assert not result["ok"] and set(result["validation_errors"]) == set(replacement)
    assert Credential._base_manager.get(pk=credential.pk).reveal() == credential.handler.clean(original)


def test_empty_static_token_is_only_a_creation_placeholder():
    credential = make_integration("empty-static-token").credential
    admin = create_platform_admin("empty-token-admin", password=None)
    result = result_data(execute_schema(_schema("console"), REPLACE, {
        "id": credential.public_id, "data": {"api_key": ""}, "confirmed": True,
    }, user=admin))["replace_credential_secret"]
    assert not result["ok"] and "api_key" in result["validation_errors"]
    assert Credential._base_manager.get(pk=credential.pk).reveal() == credential.reveal()
    created = result_data(execute_schema(_schema("console"), """mutation($data: CredentialInput!) {
      create_credential(data: $data) { id }
    }""", {"data": {"name": "Placeholder", "kind": "static_token", "api_key": ""}}, user=admin))
    assert created["create_credential"]["id"]
    assert Credential._base_manager.get(name="Placeholder").reveal() == {"api_key": ""}


@pytest.mark.parametrize("kind", ["static_token", "oauth"])
def test_expiry_is_reported_but_not_writable_by_owner_or_admin(kind):
    owner = get_user_model().objects.create_user(f"expiry-owner-{kind}")
    credential = make_integration(
        f"expiry-{kind}", kind=kind, material={"access_token": "token", "api_key": "key"},
    ).credential
    admin = create_platform_admin(f"expiry-admin-{kind}", password=None)
    with system_context(reason="test.expiry.owner"):
        credential.user = owner
        credential.save(update_fields=["user"])
    for actor in (owner, admin):
        result = execute_schema(_schema("console"), UPDATE, {
            "id": credential.public_id, "set": {"expires_at": None},
        }, user=actor)
        assert result.errors and "expires_at" in result.errors[0].message
