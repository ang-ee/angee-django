"""Knowledge reads fail closed and authored actions retain replay semantics."""

import pytest
from rebac import PermissionDenied, actor_context, system_context, to_subject_ref
from rebac.models import active_relationship_model

from angee.graphql.access import ChangeReadGate
from angee.graphql.events import ChangePayload
from angee.knowledge.schema import schemas
from tests.conftest import (
    Page,
    RecordBinding,
    Vault,
    addon_schema,
    create_platform_admin,
    create_user,
    execute_schema,
    result_data,
    vault_for,
)
from tests.knowledge_campaign import clone_template as clone_template
from tests.test_knowledge import _grant

CLONE = """
mutation Clone($template: ID!, $name: String!, $owned: Boolean!, $key: String) {
  create_vault_from(template: $template, name: $name, owned: $owned, client_creation_key: $key) {
    ok id message validation_errors
  }
}
"""


@pytest.mark.parametrize("seat", ["neither", "record", "knowledge", "both", "administrator"])
def test_binding_without_record_owner_arm_is_absent_in_every_read_direction_and_event(composed_tables, seat):
    author = create_user("binding-author")
    viewer = create_platform_admin("binding-admin") if seat == "administrator" else create_user("binding-reader")
    knowledge = vault_for(author, name="Knowledge end")
    record = vault_for(author, name="Record end without a contributed binding arm")
    with actor_context(author):
        page = Page.objects.create_in(knowledge, title="Bound page")
        page_binding = RecordBinding.objects.upsert(page=page, target=record)
        vault_binding = RecordBinding.objects.upsert(vault=knowledge, target=record)
    if seat in {"record", "both"}:
        _grant(record, "viewer", viewer)
    if seat in {"knowledge", "both"}:
        _grant(knowledge, "viewer", viewer)

    with actor_context(viewer):
        assert record.as_user(viewer).has_access("read") == (seat in {"record", "both", "administrator"})
        assert page.as_user(viewer).has_access("read") == (seat in {"knowledge", "both", "administrator"})
        assert list(RecordBinding.objects.for_record(record)) == []
        assert list(RecordBinding.objects.pages_for_record(record)) == []
        assert list(RecordBinding.objects.vaults_for_record(record)) == []
        assert list(RecordBinding.objects.for_knowledge(page)) == []
        assert list(RecordBinding.objects.for_knowledge(knowledge)) == []
        assert RecordBinding.objects.records_for_page(page) == ()
        assert RecordBinding.objects.records_for_vault(knowledge) == ()
        assert not RecordBinding.objects.exists()
    for binding in (page_binding, vault_binding):
        for action in ("create", "update", "delete"):
            event = ChangePayload.from_instance(binding, action=action, update_fields={"role"})
            assert ChangeReadGate(RecordBinding, to_subject_ref(viewer)).filter(event) is None

    schema = addon_schema(schemas, "public")
    record_result = result_data(execute_schema(
        schema,
        """query ($id: ID!) {
          record_knowledge_bindings(model_label: "knowledge.Vault", record_id: $id) { id page vault }
        }""",
        {"id": str(record.sqid)}, user=viewer,
    ))
    assert record_result["record_knowledge_bindings"] == []
    if seat in {"knowledge", "both", "administrator"}:
        reverse = result_data(execute_schema(
            schema,
            """query ($page: ID!, $vault: ID!) {
              page_record_bindings(page: $page) { id record_id }
              vault_record_bindings(vault: $vault) { id record_id }
            }""",
            {"page": str(page.sqid), "vault": str(knowledge.sqid)}, user=viewer,
        ))
        assert reverse == {"page_record_bindings": [], "vault_record_bindings": []}


def test_binding_writes_require_both_ends_and_are_role_keyed(composed_tables):
    author, writer = create_user("binding-owner"), create_user("binding-writer")
    knowledge = vault_for(author, name="Knowledge")
    record = vault_for(author, name="Record")
    with actor_context(author):
        page = Page.objects.create_in(knowledge, title="Guide")
    _grant(knowledge, "editor", writer)
    with actor_context(writer), pytest.raises(PermissionDenied):
        RecordBinding.objects.upsert(page=page, target=record)
    _grant(record, "editor", writer)
    with actor_context(writer):
        first = RecordBinding.objects.upsert(page=page, target=record)
        assert RecordBinding.objects.upsert(page=page, target=record).pk == first.pk
        other_role = RecordBinding.objects.upsert(page=page, target=record, role="reference")
        assert other_role.pk != first.pk
        assert RecordBinding.objects.unbind(page=page, target=record) == 1
    with system_context(reason="test.binding.remaining"):
        assert list(RecordBinding.objects.values_list("pk", flat=True)) == [other_role.pk]


def test_deleting_target_removes_bindings_without_deleting_knowledge(composed_tables):
    author = create_user("binding-owner")
    knowledge = vault_for(author, name="Knowledge")
    record = vault_for(author, name="Target")
    with actor_context(author):
        page = Page.objects.create_in(knowledge, title="Retained guide")
        RecordBinding.objects.upsert(page=page, target=record)
        RecordBinding.objects.upsert(vault=knowledge, target=record)
        record.delete()
    with system_context(reason="test.binding.cascade"):
        assert not RecordBinding.objects.exists()
        assert Page.objects.filter(pk=page.pk).exists()
        assert Vault.objects.filter(pk=knowledge.pk).exists()


def test_page_can_write_tracks_vault_permission_for_non_author_viewer(clone_template):
    fixture = clone_template
    schema = addon_schema(schemas, "public")
    query = "query ($id: String!) { pages_by_pk(id: $id) { id permissions } }"
    variables = {"id": str(fixture.note.sqid)}
    detail = result_data(execute_schema(schema, query, variables, user=fixture.actor))["pages_by_pk"]
    assert detail == {"id": str(fixture.note.sqid), "permissions": []}
    _grant(fixture.source, "editor", fixture.actor)
    detail = result_data(execute_schema(schema, query, variables, user=fixture.actor))["pages_by_pk"]
    assert detail["permissions"] == ["write"]
    stranger = create_user("page-stranger")
    assert result_data(execute_schema(schema, query, variables, user=stranger))["pages_by_pk"] is None


@pytest.mark.parametrize("unavailable", ["unshared", "deleted"])
@pytest.mark.parametrize("owned", [True, False])
def test_graphql_clone_replays_after_template_access_is_lost(clone_template, unavailable, owned):
    fixture = clone_template
    schema = addon_schema(schemas, "public")
    variables = {"template": str(fixture.source.sqid), "name": "API clone", "owned": owned, "key": "api"}
    first = result_data(execute_schema(schema, CLONE, variables, user=fixture.actor))["create_vault_from"]
    assert first["ok"] is True
    if owned:
        with actor_context(fixture.actor):
            clone = Vault.objects.get(client_creation_key="api")
            clone.transfer_ownership(fixture.reader)
        with actor_context(fixture.reader):
            clone.as_user(fixture.reader).transfer_ownership(None)
    with actor_context(fixture.author):
        if unavailable == "deleted":
            fixture.source.delete()
        else:
            # The actor only read through viewer; ownership transfer of the template
            # would leave that grant, so remove the actual sharing fact.
            with system_context(reason="test.template.unshare"):
                active_relationship_model().objects.filter(
                    resource_type="knowledge/vault", resource_id=str(fixture.source.pk), relation="viewer",
                ).delete()
    replay = result_data(execute_schema(schema, CLONE, variables, user=fixture.actor))["create_vault_from"]
    assert replay == first
    with system_context(reason="test.api.replay_count"):
        assert Vault.objects.filter(client_creation_key="api").count() == 1


def test_graphql_changed_clone_request_has_creation_key_conflict_code(clone_template):
    fixture = clone_template
    schema = addon_schema(schemas, "public")
    variables = {"template": str(fixture.source.sqid), "name": "API clone", "owned": False, "key": "api"}
    assert result_data(execute_schema(schema, CLONE, variables, user=fixture.actor))["create_vault_from"]["ok"]
    conflict = execute_schema(schema, CLONE, {**variables, "name": "Changed"}, user=fixture.actor)
    assert conflict.errors is not None
    assert conflict.errors[0].extensions["code"] == "CREATION_KEY_CONFLICT"


@pytest.mark.parametrize("caller", ["anonymous", "unreadable", "malformed"])
def test_graphql_clone_rejects_unauthorized_or_malformed_requests_without_creating_rows(clone_template, caller):
    fixture = clone_template
    schema = addon_schema(schemas, "public")
    actor = None if caller == "anonymous" else create_user("stranger") if caller == "unreadable" else fixture.actor
    template = "not-a-vault-id" if caller == "malformed" else str(fixture.source.sqid)
    result = execute_schema(
        schema, CLONE, {"template": template, "name": "Denied", "owned": True, "key": "denied"}, user=actor,
    )
    if caller == "anonymous":
        assert result.errors is not None
        assert "Authentication required" in result.errors[0].message
    else:
        outcome = result_data(result)["create_vault_from"]
        assert outcome["ok"] is False
        # The shared action guard projects missing/unreadable objects as a
        # message-only domain failure; it need not invent field validation.
        assert outcome["id"] is None
        assert outcome["message"]
    with system_context(reason="test.api.denied_count"):
        assert not Vault.objects.filter(client_creation_key="denied").exists()
