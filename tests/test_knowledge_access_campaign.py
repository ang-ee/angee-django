"""Knowledge reads fail closed and authored actions retain replay semantics."""

import pytest
from django.contrib.auth.models import AnonymousUser
from django.contrib.contenttypes.models import ContentType
from django.db import connection
from django.test.utils import CaptureQueriesContext
from rebac import (
    ObjectRef,
    PermissionDenied,
    RelationshipTuple,
    actor_context,
    system_context,
    to_subject_ref,
    write_relationships,
)
from rebac.testing import assert_decisions

from angee.base.testing.queries import statement_startswith
from angee.graphql.access import ChangeReadGate
from angee.graphql.events import ChangePayload
from angee.graphql.testing.documents import execute_schema, result_data
from angee.knowledge.schema import schemas
from angee.messaging.testing.models import ThreadAttachment
from angee.projects.testing.models import Project, Task
from angee.provenance.context import changeset, changeset_model
from angee.tags.testing.models import Tag, TagAssignment
from tests.conftest import (
    Link,
    MarkdownPage,
    Page,
    RecordBinding,
    Vault,
    addon_schema,
    create_platform_admin,
    create_user,
    vault_for,
)
from tests.decision_table import seat_cells
from tests.extcontrib.models import Record
from tests.knowledge_campaign import clone_template as clone_template
from tests.test_knowledge import _grant

CLONE = """
mutation Clone($template: ID!, $name: String!, $owned: Boolean!, $key: String) {
  create_vault_from(template: $template, name: $name, owned: $owned, idempotency_key: $key) {
    ok id message validation_errors
  }
}
"""


def test_the_policy_decides_every_knowledge_permission(composed_tables):
    """One world, every knowledge permission and extension arm with an allow and a deny, and its read scopes."""

    seats = {name: create_user(f"knowledge-policy-{name}") for name in (
        "owner", "editor", "viewer", "page-viewer", "role-viewer", "role-editor", "vault-admin",
        "knowledge-admin", "task-reader", "both", "outsider",
    )}
    seats.update(admin=create_platform_admin("knowledge-policy-admin"), anonymous=AnonymousUser())
    owner = seats["owner"]
    vault, trashed_vault = vault_for(owner, name="Policy"), vault_for(owner, name="Trashed")
    with actor_context(owner):
        page = Page.objects.create_in(vault, title="Folder", kind=Page.PageKind.FOLDER)
        child = Page.objects.create_in(vault, title="Child", parent=page)
        Page.objects.create_in(vault, title="Target")
        MarkdownPage.objects.write_body(child, "see [[Target]]")
        trashed_page = Page.objects.create_in(vault, title="Withdrawn")
    with system_context(reason="test.knowledge.policy.world"):
        markdown = MarkdownPage._base_manager.get(page_ptr=child)
        link = Link._base_manager.get(source_page=child)
        trashed_page.trash(reason="Withdrawn")
        trashed_vault.trash(reason="Withdrawn")
        project = Project.objects.create(title="Bound record")
        task = Task.objects.create(project=project, title="Bound task", assignee=seats["both"])
        binding = RecordBinding.objects.upsert(page=page, target=task)
        chatter = ThreadAttachment.objects.ensure_for_record(page)
        tag = TagAssignment.objects.create(
            tag=Tag.objects.create(name="Policy"), content_type=ContentType.objects.get_for_model(Page),
            object_id=page.pk,
        )
        for role in ("vault_viewer", "vault_editor", "vault_admin", "knowledge_admin"):
            seat = {"vault_viewer": "role-viewer", "vault_editor": "role-editor"}.get(role, role.replace("_", "-"))
            write_relationships([RelationshipTuple(ObjectRef("knowledge/role", role), "member",
                                                   to_subject_ref(seats[seat]))])
    for row, relation, seat in (
        (vault, "editor", "editor"), (vault, "viewer", "viewer"), (trashed_vault, "viewer", "viewer"),
        (trashed_vault, "editor", "editor"), (page, "viewer", "page-viewer"), (project, "reader", "task-reader"),
        (vault, "viewer", "both"),
    ):
        _grant(row, relation, seats[seat])

    roles = "role-viewer role-editor vault-admin knowledge-admin"
    cells = seat_cells(
        seats.__getitem__,
        # knowledge/vault, live and trashed
        (vault, "create", "outsider", "anonymous"),
        (vault, "read_granted", f"owner editor viewer {roles} admin", "outsider anonymous"),
        (vault, "write_granted", "owner editor role-editor vault-admin knowledge-admin admin", "viewer role-viewer"),
        (vault, "delete", "owner vault-admin knowledge-admin admin", "editor role-editor"),
        (vault, "read", "viewer", "outsider"),
        (vault, "write", "editor", "viewer"),
        (trashed_vault, "conceal", "outsider", "anonymous"),
        (trashed_vault, "withheld", "outsider", "anonymous"),
        (vault, "withheld", "", "outsider"),
        (trashed_vault, "read", "owner", "viewer"),
        (trashed_vault, "write", "owner", "editor"),
        (vault, "read_as_subject", "viewer role-viewer", "outsider"),
        (vault, "write_as_subject", "editor role-editor", "viewer"),
        (vault, "transfer", "owner admin", "editor"),
        (vault, "share", "owner admin", "editor"),
        (vault, "write__owner", "owner", "editor"),
        # knowledge/page: vault and parent grants, trash
        (page, "read_granted", "viewer page-viewer", "outsider"),
        (child, "read_granted", "page-viewer", "outsider"),
        (page, "write_granted", "editor", "viewer page-viewer"),
        (page, "create", "editor", "viewer"),
        (page, "read", "viewer page-viewer", "outsider anonymous"),
        (page, "write", "editor", "viewer"),
        (trashed_page, "conceal", "outsider", "anonymous"),
        (trashed_page, "withheld", "outsider", "anonymous"),
        (page, "withheld", "", "outsider"),
        (trashed_page, "read", "owner", "viewer"),
        (trashed_page, "write", "owner", "editor"),
        (page, "read_as_subject", "page-viewer", "outsider"),
        (page, "write_as_subject", "editor", "viewer"),
        (page, "delete", "owner", "editor"),
        # knowledge/markdown_page and knowledge/link follow their page
        (markdown, "read", "page-viewer", "outsider"),
        *((markdown, action, "editor", "viewer") for action in ("create", "write", "delete")),
        (link, "read", "page-viewer", "outsider"),
        *((link, action, "editor", "viewer") for action in ("write", "delete")),
        # knowledge/record_binding needs both ends
        (binding, "target_read", "task-reader", "outsider"),
        (binding, "target_write", "both", "task-reader"),
        (binding, "read", "both", "task-reader viewer"),
        (binding, "create", "admin", "both editor"),
        (binding, "write", "", "admin"),
        (binding, "delete", "admin", "both"),
        # extension arms: page tags and page chatter
        (tag, "target_read", "viewer", "outsider"),
        (tag, "target_write", "editor", "viewer"),
        (chatter, "target_write", "editor", "viewer"),
        (chatter.thread, "chatter_read", "viewer", "outsider"),
    )
    assert_decisions(
        checks=cells,
        accessible=(
            (seats["page-viewer"], "read", Page, (page, child)),
            (seats["viewer"], "read", Vault, (vault,)),
            (seats["outsider"], "read", Vault, ()),
        ),
    )


def test_record_note_is_atomic_and_replays_the_original_binding(composed_tables, monkeypatch):
    owner = create_user("record-note-owner")
    vault = vault_for(owner, name="Notes")
    target = vault_for(owner, name="Target")
    manager = RecordBinding.objects
    with actor_context(owner):
        def fail_binding(*args, **kwargs):
            raise RuntimeError("Binding unavailable")

        monkeypatch.setattr(manager, "upsert", fail_binding)
        with pytest.raises(RuntimeError, match="Binding unavailable"):
            manager.create_note(target=target, vault=vault, body="A note\nDetails", idempotency_key="post-note")
        assert not Page.objects.filter(vault=vault).exists()
        assert not changeset_model().objects.filter(key="post-note").exists()
        monkeypatch.delattr(manager, "upsert")
        binding = manager.create_note(target=target, vault=vault, body="A note\nDetails", idempotency_key="post-note")
        replay = manager.create_note(target=target, vault=vault, body="A note\nDetails", idempotency_key="post-note")
        assert replay.pk == binding.pk
        assert Page.objects.filter(vault=vault).count() == 1
        assert MarkdownPage.objects.get(pk=binding.page_id).body == "A note\nDetails"


@pytest.mark.parametrize("missing_end", ["vault", "target"])
def test_record_note_denial_leaves_no_page_or_receipt(composed_tables, missing_end):
    owner, writer = create_user("note-owner"), create_user("note-writer")
    vault = vault_for(owner, name="Notes")
    target = vault_for(owner, name="Target")
    _grant(target if missing_end == "vault" else vault, "editor", writer)
    with actor_context(writer), pytest.raises(PermissionDenied):
        RecordBinding.objects.create_note(target=target, vault=vault, body="Denied", idempotency_key="denied-note")
    with system_context(reason="test.note.rollback"):
        assert not Page.objects.filter(vault=vault).exists()
        assert not changeset_model().objects.filter(key="denied-note").exists()


def test_record_pages_projection_respects_role_and_both_write_ends(composed_tables):
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
    # The test consumer declares vaults bindable; the no-arm regression below proves
    # binding reads fail closed for a record type no fragment declares (extcontrib.Record).
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


@pytest.mark.parametrize("seat", ["both", "administrator"])
def test_binding_without_record_owner_arm_is_absent_in_every_read_direction_and_event(composed_tables, seat):
    """Even an actor who reads both ends, or an administrator, sees no binding to an unarmed record type."""
    # No record_binding fragment names this type; a fragment that does breaks the premise here.
    assert not RecordBinding.declares_target(Record)
    author = create_user("binding-author")
    viewer = create_platform_admin("binding-admin") if seat == "administrator" else create_user("binding-reader")
    knowledge = vault_for(author, name="Knowledge end")
    with system_context(reason="test.binding.undeclared_record"):
        record = Record.objects.create(name="Record end without a contributed binding arm")
    _grant(record, "writer", author)
    with actor_context(author):
        page = Page.objects.create_in(knowledge, title="Bound page")
        # Writable on both ends, but no relation names the record's type.
        with pytest.raises(PermissionDenied):
            RecordBinding.objects.upsert(page=page, target=record)
    with system_context(reason="test.binding.undeclared_seed"):
        page_binding = RecordBinding.objects.upsert(page=page, target=record)
        vault_binding = RecordBinding.objects.upsert(vault=knowledge, target=record)
    if seat in {"record", "both"}:
        _grant(record, "reader", viewer)
    if seat in {"knowledge", "both"}:
        _grant(knowledge, "viewer", viewer)

    with actor_context(viewer):
        assert record.as_user(viewer).has_access("read") == (seat in {"record", "both", "administrator"})
        assert page.as_user(viewer).has_access("read") == (seat in {"knowledge", "both", "administrator"})
        assert list(RecordBinding.objects.for_record(record)) == []
        assert list(RecordBinding.objects.pages_for_record(record)) == []
        assert list(RecordBinding.objects.for_knowledge(page)) == []
        assert list(RecordBinding.objects.for_knowledge(knowledge)) == []
        assert not RecordBinding.objects.exists()
    for binding in (page_binding, vault_binding):
        for action in ("create", "update", "delete"):
            event = ChangePayload.from_instance(binding, action=action, update_fields={"role"})
            assert ChangeReadGate(RecordBinding, to_subject_ref(viewer)).filter(event) is None

    schema = addon_schema(schemas, "public")
    record_result = result_data(execute_schema(
        schema,
        """query ($id: ID!) {
          record_knowledge_bindings(model_label: "extcontrib.Record", record_id: $id) { id page vault }
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
        assert sum(statement_startswith(query["sql"], f"SELECT {table}.") for query in queries) == 1
        with pytest.raises(PermissionDenied):
            RecordBinding.objects.unbind(page=page, target=record)
    with actor_context(writer):
        assert RecordBinding.objects.unbind(page=page, target=record) == 1
    with system_context(reason="test.binding.remaining"):
        assert list(RecordBinding.objects.values_list("pk", flat=True)) == [other_role.pk]


def test_binding_selections_chain_on_a_selected_population_and_keep_its_actor(composed_tables):
    author, stranger = create_user("binding-chain-author"), create_user("binding-chain-stranger")
    knowledge = vault_for(author, name="Chain knowledge")
    record = vault_for(author, name="Chain record")
    with actor_context(author):
        page = Page.objects.create_in(knowledge, title="Chained")
        RecordBinding.objects.upsert(page=page, target=record)
        reference = RecordBinding.objects.upsert(page=page, target=record, role="reference")
        vault_reference = RecordBinding.objects.upsert(vault=knowledge, target=record, role="reference")

    selected = RecordBinding.objects.with_actor(author).filter(role="reference")
    assert set(selected.for_record(record).values_list("pk", flat=True)) == {reference.pk, vault_reference.pk}
    assert list(selected.pages_for_record(record).values_list("pk", flat=True)) == [reference.pk]
    assert list(selected.for_knowledge(page).values_list("pk", flat=True)) == [reference.pk]
    assert not RecordBinding.objects.with_actor(stranger).filter(role="reference").for_record(record).exists()


def test_binding_refuses_undeclared_and_untyped_targets(composed_tables):
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


def test_graphql_clone_replays_after_template_access_is_lost(clone_template):
    """The wire returns the original result once the template is gone; ownership and unsharing are proved
    on the clone verb."""

    fixture = clone_template
    schema = addon_schema(schemas, "public")
    variables = {"template": str(fixture.source.sqid), "name": "API clone", "owned": True, "key": "api"}
    first = result_data(execute_schema(schema, CLONE, variables, user=fixture.actor))["create_vault_from"]
    assert first["ok"] is True
    with actor_context(fixture.author):
        fixture.source.delete()
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
def test_record_note_and_binding_keep_locks_replay_and_rollback(
    composed_tables, monkeypatch, bound_manager,
):
    """Admission locks, replay, and failed writes use the model's database."""

    owner = create_user("record-note-lock-owner")
    vault = vault_for(owner, name="Notes")
    target = vault_for(owner, name="Target")
    manager = RecordBinding.objects.db_manager("default") if bound_manager else RecordBinding.objects
    with actor_context(owner), changeset(fresh=True):
        with CaptureQueriesContext(connection) as queries:
            binding = manager.create_note(
                target=target, vault=vault, body="Note", idempotency_key="note",
            )
        assert binding._state.db == "default"
        replay = manager.create_note(
            target=target, vault=vault, body="Note", idempotency_key="note",
        )
        assert replay.pk == binding.pk
        page = Page.objects.get(pk=binding.page_id)
        assert manager.upsert(target=target, page=page).pk == binding.pk
        if connection.vendor == "postgresql":
            locks = [row["sql"] for row in queries if "FOR NO KEY UPDATE" in row["sql"]]
            assert any(Vault._meta.db_table in sql for sql in locks)
            assert any(Page._meta.db_table in sql for sql in locks)

        def fail_binding(*args, **kwargs):
            raise RuntimeError("Binding unavailable")

        monkeypatch.setattr(manager, "upsert", fail_binding)
        with pytest.raises(RuntimeError, match="Binding unavailable"):
            manager.create_note(
                target=target, vault=vault, body="Must roll back", idempotency_key="rollback",
            )
        monkeypatch.delattr(manager, "upsert")
        assert Page.objects.filter(vault=vault).count() == 1
        assert not changeset_model().objects.filter(key="rollback").exists()
