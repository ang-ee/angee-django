"""Knowledge reads fail closed and authored actions retain replay semantics."""

import pytest
from django.contrib.contenttypes.models import ContentType
from django.db import connection, connections, router, transaction
from django.test import TestCase
from django.test.utils import CaptureQueriesContext
from rebac import PermissionDenied, actor_context, system_context, to_subject_ref
from rebac.models import active_relationship_model

from angee.base.refs import RecordRef
from angee.graphql.access import ChangeReadGate
from angee.graphql.events import ChangePayload
from angee.base import signals as model_signals
from angee.base.models import merge_references
from angee.base.models.merge_references import EdgeReference, references
from angee.knowledge.models import bindings as binding_owner
from angee.knowledge.schema import schemas
from angee.provenance.context import changeset, changeset_model
from tests.conftest import (
    MarkdownPage,
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
  create_vault_from(template: $template, name: $name, owned: $owned, idempotency_key: $key) {
    ok id message validation_errors
  }
}
"""


def test_record_note_is_atomic_and_replays_the_original_binding(composed_permissions, monkeypatch):
    owner = create_user("record-note-owner")
    vault = vault_for(owner, name="Notes")
    target = vault_for(owner, name="Target")
    manager = RecordBinding.objects
    upsert = type(manager).upsert
    with actor_context(owner):
        def fail_binding(*args, **kwargs):
            raise RuntimeError("Binding unavailable")

        monkeypatch.setattr(type(manager), "upsert", fail_binding)
        with pytest.raises(RuntimeError, match="Binding unavailable"):
            manager.create_note(target=target, vault=vault, body="A note\nDetails", idempotency_key="post-note")
        assert not Page.objects.filter(vault=vault).exists()
        assert not changeset_model().objects.filter(key="post-note").exists()
        monkeypatch.setattr(type(manager), "upsert", upsert)
        binding = manager.create_note(target=target, vault=vault, body="A note\nDetails", idempotency_key="post-note")
        replay = manager.create_note(target=target, vault=vault, body="A note\nDetails", idempotency_key="post-note")
        assert replay.pk == binding.pk
        assert Page.objects.filter(vault=vault).count() == 1
        assert MarkdownPage.objects.get(pk=binding.page_id).body == "A note\nDetails"


def test_binding_changes_identify_the_parent_without_loading_page_content(composed_permissions):
    owner = create_user("binding-change-owner")
    vault = vault_for(owner, name="Notes")
    target = vault_for(owner, name="Target")
    with actor_context(owner):
        page = Page.objects.create_in(vault, title="Note")
        binding = RecordBinding.objects.upsert(page=page, target=target, role="notes")
        reference = RecordRef.from_instance(target)
        assert reference in binding.change_related_records()
        for action in ("create", "delete"):
            event = ChangePayload.from_instance(binding, action=action, update_fields=None)
            assert (reference.model_label, reference.public_id) in {
                (related.model, related.id) for related in event.related_records
            }
            visible = ChangeReadGate(RecordBinding, to_subject_ref(owner)).filter(event)
            assert visible is not None
            assert (reference.model_label, reference.public_id) in {
                (related.model, related.id) for related in visible.related_records
            }


@pytest.mark.parametrize("rolled_back", [False, True])
def test_binding_retarget_observes_only_old_and_new_parent_scopes_after_commit(
    composed_permissions, monkeypatch, rolled_back,
):
    owner = create_user("binding-retarget-owner")
    vault = vault_for(owner, name="Notes")
    before_target = vault_for(owner, name="Before")
    after_target = vault_for(owner, name="After")
    unrelated = vault_for(owner, name="Unrelated")
    with actor_context(owner):
        page = Page.objects.create_in(vault, title="Moved note")
        binding = RecordBinding.objects.upsert(page=page, target=before_target, role="notes")
        untouched = RecordBinding.objects.upsert(page=page, target=unrelated, role="notes")
    delivered = []

    def queue_identity(instance, *, action, update_fields, **kwargs):
        if not isinstance(instance, RecordBinding):
            return
        assert action == "update" and update_fields is None
        identities = instance.change_related_records()
        transaction.on_commit(lambda: delivered.append((instance.pk, identities)), using=instance._state.db)

    monkeypatch.setattr(model_signals, "publish_change", queue_identity)
    model_signals.connect_observers(RecordBinding)
    reference = next(item for item in references(type(before_target)) if item.model is RecordBinding and isinstance(item, EdgeReference))
    with TestCase.captureOnCommitCallbacks(execute=True):
        try:
            with changeset(), transaction.atomic(), system_context(reason="test.binding.native_retarget"):
                reference.move(reference.rows(before_target), survivor=after_target)
                assert delivered == []
                if rolled_back:
                    raise RuntimeError("Refused later in the fold")
        except RuntimeError:
            if not rolled_back:
                raise
    if rolled_back:
        assert delivered == []
    else:
        assert len(delivered) == 1
        assert delivered[0][0] == binding.pk
        assert set(delivered[0][1]) >= {
            RecordRef.from_instance(before_target), RecordRef.from_instance(after_target), RecordRef.from_instance(page),
        }
    with system_context(reason="test.binding.retarget_result"):
        binding.refresh_from_db()
        untouched.refresh_from_db()
        assert binding.object_id == (before_target.pk if rolled_back else after_target.pk)
        assert untouched.object_id == unrelated.pk


def test_binding_native_move_batches_sparse_facts_and_resolves_lineage_once_per_batch(
    composed_permissions, monkeypatch,
):
    owner = create_user("binding-batches-owner")
    vault = vault_for(owner, name="Notes")
    before_target = vault_for(owner, name="Before")
    after_target = vault_for(owner, name="After")
    with actor_context(owner):
        for index in range(11):
            page = Page.objects.create_in(vault, title=f"Note {index}")
            RecordBinding.objects.upsert(page=page, target=before_target, role="notes")
    batches = []
    published = []
    expansions = []
    expand = binding_owner.record_view_refs

    def record_expansion(*identities, **kwargs):
        expansions.append(len(identities))
        return expand(*identities, **kwargs)

    def observe_batch(sender, *, instances, previous_instances, **kwargs):
        batches.append(len(instances))
        assert len(instances) == len(previous_instances)
        for previous in previous_instances:
            assert previous.get_deferred_fields() >= {"page_id", "vault_id", "created_at", "updated_at"}

    def publish(instance, *, action, update_fields, **kwargs):
        if isinstance(instance, RecordBinding):
            assert update_fields is None
            assert instance.get_deferred_fields() >= {"created_at", "updated_at", "role"}
            assert instance._change_related_refs is not None
            assert instance.change_related_records()
            published.append(instance.pk)

    refresh = RecordBinding.refresh_from_db

    def reject_deferred_scope(self, *args, fields=None, **kwargs):
        if fields is not None and set(fields) & {"content_type_id", "object_id", "page_id", "vault_id", "id"}:
            raise AssertionError("Bulk projection reloaded a deferred identity field")
        return refresh(self, *args, fields=fields, **kwargs)

    monkeypatch.setattr(RecordBinding, "refresh_from_db", reject_deferred_scope)
    monkeypatch.setattr(merge_references, "query_batch_size", lambda _query: 7)
    monkeypatch.setattr(binding_owner, "record_view_refs", record_expansion)
    monkeypatch.setattr(model_signals, "publish_change", publish)
    model_signals.connect_observers(RecordBinding)
    model_signals.bulk_updated.connect(observe_batch, sender=RecordBinding, weak=False)
    reference = next(item for item in references(type(before_target)) if item.model is RecordBinding and isinstance(item, EdgeReference))
    try:
        with changeset(), system_context(reason="test.binding.bounded_move"):
            reference.move(reference.rows(before_target), survivor=after_target)
    finally:
        model_signals.bulk_updated.disconnect(observe_batch, sender=RecordBinding)
    assert sum(batches) == 11 and len(batches) > 1 and max(batches) <= 7
    assert len(published) == len(set(published)) == 11
    assert len(expansions) == len(batches)


def test_binding_move_collapses_only_identity_collisions_and_keeps_source_gates(composed_permissions, monkeypatch):
    owner = create_user("binding-collisions-owner")
    vault = vault_for(owner, name="Notes")
    before_target = vault_for(owner, name="Before")
    after_target = vault_for(owner, name="After")
    with actor_context(owner):
        page = Page.objects.create_in(vault, title="Shared note")
        removed = RecordBinding.objects.upsert(page=page, target=before_target, role="notes")
        kept = RecordBinding.objects.upsert(page=page, target=after_target, role="notes")
        moved = RecordBinding.objects.upsert(page=page, target=before_target, role="reference")
    events = []

    def publish(instance, *, action, update_fields, **kwargs):
        if isinstance(instance, RecordBinding):
            events.append((instance.pk, action, instance.change_related_records()))

    monkeypatch.setattr(model_signals, "publish_change", publish)
    model_signals.connect_observers(RecordBinding)
    reference = next(item for item in references(type(before_target)) if item.model is RecordBinding and isinstance(item, EdgeReference))
    with changeset(), system_context(reason="test.binding.collision_move"):
        reference.move(reference.rows(before_target), survivor=after_target)
    assert [(pk, action) for pk, action, _refs in events] == [(removed.pk, "delete"), (moved.pk, "update")]
    assert set(events[-1][2]) >= {RecordRef.from_instance(before_target), RecordRef.from_instance(after_target), RecordRef.from_instance(page)}
    with system_context(reason="test.binding.collision_result"):
        assert not RecordBinding._base_manager.filter(pk=removed.pk).exists()
        assert RecordBinding._base_manager.get(pk=kept.pk).object_id == after_target.pk
        assert RecordBinding._base_manager.get(pk=moved.pk).object_id == after_target.pk


@pytest.mark.parametrize("missing_end", ["vault", "target"])
def test_record_note_denial_leaves_no_page_or_receipt(composed_permissions, missing_end):
    owner, writer = create_user("note-owner"), create_user("note-writer")
    vault = vault_for(owner, name="Notes")
    target = vault_for(owner, name="Target")
    _grant(target if missing_end == "vault" else vault, "editor", writer)
    with actor_context(writer), pytest.raises(PermissionDenied):
        RecordBinding.objects.create_note(target=target, vault=vault, body="Denied", idempotency_key="denied-note")
    with system_context(reason="test.note.rollback"):
        assert not Page.objects.filter(vault=vault).exists()
        assert not changeset_model().objects.filter(key="denied-note").exists()


def test_record_pages_projection_respects_role_and_both_write_ends(composed_permissions):
    owner, reader = create_user("page-owner"), create_user("page-reader")
    vault = vault_for(owner, name="Pages")
    record = vault_for(owner, name="Record")
    with actor_context(owner):
        page = Page.objects.create_in(vault, title="Guide")
        MarkdownPage.objects.write_body(page, "A private note")
        binding = RecordBinding.objects.upsert(page=page, target=record, role="reference")
        RecordBinding.objects.upsert(page=page, target=record, role="related")
    _grant(record, "viewer", reader)
    _grant(vault, "viewer", reader)
    # The test consumer declares vaults bindable; the no-arm regression below
    # proves binding reads fail closed for an undeclared record type.
    schema = addon_schema(schemas, "console")
    query = """query ($id: ID!, $role: String) {
      record_knowledge_bindings(model_label: "knowledge.Vault", record_id: $id, role: $role) {
        id page page_title page_can_write role
        page_detail { id title created_at created_by_label markdown { body } }
      }
      record_knowledge_can_bind(model_label: "knowledge.Vault", record_id: $id)
    }"""
    variables = {"id": str(record.sqid), "role": "reference"}
    visible = result_data(execute_schema(schema, query, variables, user=reader))
    assert visible["record_knowledge_can_bind"] is False
    assert len(visible["record_knowledge_bindings"]) == 1
    binding_view = visible["record_knowledge_bindings"][0]
    assert {key: binding_view[key] for key in ("id", "page", "page_title", "page_can_write", "role")} == {
        "id": str(binding.sqid), "page": str(page.sqid), "page_title": "Guide",
        "page_can_write": False, "role": "reference",
    }
    detail = binding_view["page_detail"]
    assert detail["id"] == str(page.sqid)
    assert detail["title"] == "Guide"
    assert detail["created_at"]
    assert detail["created_by_label"]
    assert detail["markdown"] == {"body": "A private note"}
    owner_view = result_data(execute_schema(schema, query, variables, user=owner))
    assert owner_view["record_knowledge_can_bind"] is True
    assert owner_view["record_knowledge_bindings"][0]["page_can_write"] is True


@pytest.mark.parametrize("seat", ["neither", "record", "knowledge", "both", "administrator"])
def test_binding_without_record_owner_arm_is_absent_in_every_read_direction_and_event(composed_tables, seat):
    author = create_user("binding-author")
    viewer = create_platform_admin("binding-admin") if seat == "administrator" else create_user("binding-reader")
    knowledge = vault_for(author, name="Knowledge end")
    record = vault_for(author, name="Record end without a contributed binding arm")
    with actor_context(author):
        page = Page.objects.create_in(knowledge, title="Bound page")
        # Writable on both ends, but no relation names the record's type.
        with pytest.raises(PermissionDenied):
            RecordBinding.objects.upsert(page=page, target=record)
    with system_context(reason="test.binding.undeclared_seed"):
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


def test_binding_writes_require_both_ends_and_are_role_keyed(composed_permissions):
    author, writer = create_user("binding-owner"), create_user("binding-writer")
    record_writer, reader = create_user("binding-record-writer"), create_user("binding-reader")
    knowledge = vault_for(author, name="Knowledge")
    record = vault_for(author, name="Record")
    with actor_context(author):
        page = Page.objects.create_in(knowledge, title="Guide")
    _grant(knowledge, "editor", writer)
    _grant(record, "editor", record_writer)
    for actor in (writer, record_writer):
        with actor_context(actor), pytest.raises(PermissionDenied):
            RecordBinding.objects.upsert(page=page, target=record)
    _grant(record, "editor", writer)
    with actor_context(writer):
        first = RecordBinding.objects.upsert(page=page, target=record)
        assert first.created_by_id == writer.pk
        assert RecordBinding.objects.upsert(page=page, target=record).pk == first.pk
        other_role = RecordBinding.objects.upsert(page=page, target=record, role="reference")
        assert other_role.pk != first.pk
    _grant(knowledge, "viewer", reader)
    _grant(record, "viewer", reader)
    with actor_context(reader):
        with CaptureQueriesContext(connection) as queries:
            listed = set(RecordBinding.objects.for_record(record).values_list("pk", flat=True))
        assert listed == {first.pk, other_role.pk}
        table = connection.ops.quote_name(RecordBinding._meta.db_table)
        assert sum(query["sql"].startswith(f"SELECT {table}.") for query in queries) == 1
        with pytest.raises(PermissionDenied):
            RecordBinding.objects.unbind(page=page, target=record)
    with actor_context(writer):
        assert RecordBinding.objects.unbind(page=page, target=record) == 1
    with system_context(reason="test.binding.remaining"):
        assert list(RecordBinding.objects.values_list("pk", flat=True)) == [other_role.pk]


def test_binding_refuses_undeclared_and_untyped_targets(composed_permissions):
    author = create_user("binding-target-owner")
    knowledge = vault_for(author, name="Knowledge")
    with actor_context(author):
        page = Page.objects.create_in(knowledge, title="Writable but not bindable")
        assert page.has_access("write") and not RecordBinding.declares_target(Page)
        with pytest.raises(PermissionDenied):
            RecordBinding.objects.upsert(vault=knowledge, target=page)
        with pytest.raises(ValueError, match="no resource type"):
            RecordBinding.objects.upsert(vault=knowledge, target=ContentType.objects.get_for_model(Vault))
    with system_context(reason="test.binding.refused"):
        assert not RecordBinding.objects.exists()
    # The writer is not offered a bind its type cannot carry; a declared type still is.
    query = """query ($model: String!, $id: ID!) { record_knowledge_can_bind(model_label: $model, record_id: $id) }"""
    schema = addon_schema(schemas, "console")
    for model, record, expected in (("knowledge.Page", page, False), ("knowledge.Vault", knowledge, True)):
        result = result_data(execute_schema(schema, query, {"model": model, "id": str(record.sqid)}, user=author))
        assert result["record_knowledge_can_bind"] is expected, model


def test_deleting_target_removes_bindings_without_deleting_knowledge(composed_permissions):
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
            clone = changeset_model()._base_manager.get(
                scope=f"knowledge.vault.clone:{fixture.actor.pk}", key="api",
            ).created_row
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
        assert changeset_model()._base_manager.filter(
            scope=f"knowledge.vault.clone:{fixture.actor.pk}", key="api",
        ).count() == 1


def test_graphql_changed_clone_request_has_idempotency_conflict_code(clone_template):
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
        assert not changeset_model()._base_manager.filter(
            scope=f"knowledge.vault.clone:{fixture.actor.pk}", key="denied",
        ).exists()


@pytest.mark.parametrize("bound_manager", [False, True])
def test_record_note_and_binding_keep_secondary_locks_and_rollback(
    composed_permissions, monkeypatch, bound_manager, django_db_blocker,
):
    """Routed and explicitly bound admission lock, replay and roll back on the selected connection."""

    owner = create_user("secondary-record-note-owner")
    vault = vault_for(owner, name="Secondary notes")
    target = vault_for(owner, name="Secondary target")
    alias = "knowledge_secondary"
    # A second connection to the native test DB exposes an atomic(default)
    # mistake on PostgreSQL: the selected connection remains in autocommit.
    connections.databases[alias] = {**connections.databases["default"]}
    selected = connections[alias]
    with django_db_blocker.unblock():
        selected.ensure_connection()

    class SelectedRouter:
        def db_for_read(self, model, **hints):
            return alias

        db_for_write = db_for_read

    try:
        monkeypatch.setattr(router, "routers", [SelectedRouter()])
        owner = type(owner)._base_manager.using(alias).get(pk=owner.pk)
        vault = Vault._base_manager.using(alias).get(pk=vault.pk)
        target = Vault._base_manager.using(alias).get(pk=target.pk)
        manager = RecordBinding.objects.db_manager(alias) if bound_manager else RecordBinding.objects
        upsert = type(manager).upsert
        with (
            actor_context(owner), changeset(fresh=True, using=alias),
            CaptureQueriesContext(connection) as default_queries,
        ):
            with CaptureQueriesContext(selected) as selected_queries:
                binding = manager.create_note(
                    target=target, vault=vault, body="Secondary note", idempotency_key="secondary-note",
                )
            assert binding._state.db == alias
            replay = manager.create_note(
                target=target, vault=vault, body="Secondary note", idempotency_key="secondary-note",
            )
            assert replay.pk == binding.pk
            page = Page.objects.using(alias).get(pk=binding.page_id)
            assert manager.upsert(target=target, page=page).pk == binding.pk
            if selected.vendor == "postgresql":
                locks = [row["sql"] for row in selected_queries if "FOR NO KEY UPDATE" in row["sql"]]
                assert any(Vault._meta.db_table in sql for sql in locks)
                assert any(Page._meta.db_table in sql for sql in locks)

            def fail_binding(*args, **kwargs):
                raise RuntimeError("Secondary binding unavailable")

            monkeypatch.setattr(type(manager), "upsert", fail_binding)
            with pytest.raises(RuntimeError, match="Secondary binding unavailable"):
                manager.create_note(
                    target=target, vault=vault, body="Must roll back", idempotency_key="secondary-rollback",
                )
            monkeypatch.setattr(type(manager), "upsert", upsert)
            assert Page.objects.using(alias).filter(vault=vault).count() == 1
            assert not changeset_model().objects.using(alias).filter(key="secondary-rollback").exists()
            assert not [row["sql"] for row in default_queries if row["sql"].split()[0] in {
                "SELECT", "INSERT", "UPDATE", "DELETE",
            }]
    finally:
        monkeypatch.undo()
        selected.close()
        del connections[alias]
        del connections.databases[alias]
