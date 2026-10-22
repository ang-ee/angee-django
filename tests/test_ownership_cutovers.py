"""The W8 8.5 ownership cutovers, from the released schema, with rows, on SQLite."""

from __future__ import annotations

import logging
from pathlib import Path

import pytest
from django.apps import apps
from django.conf import settings
from django.contrib.contenttypes.models import ContentType
from django.db import connection, migrations, models
from django.db.migrations.autodetector import MigrationAutodetector
from django.db.migrations.graph import MigrationGraph
from django.db.migrations.state import ModelState, ProjectState
from rebac.models import RebacResource, Relationship, RelationshipRegistry

from angee.agents.models import AgentSession
from angee.agents.runtime_migrations import session_user
from angee.compose.migrations import RuntimeMigrations, ScheduledMigration
from angee.integrate.runtime_migrations import external_account_owner
from angee.models import OwnerMixin
from angee.parties.models import Handle
from angee.parties.runtime_migrations import handle_controller
from angee.parties.runtime_migrations import owner_column as parties_owner_column
from angee.workflows.runtime_migrations import owner_column as workflows_owner_column
from tests.tables import model_tables

LABELS = ("agents", "parties", "workflows", "integrate")
ORIGINS = frozenset({
    "angee.agents:session_user",
    "angee.parties:handle_controller",
    "angee.parties:owner_column",
    "angee.workflows:owner_column",
    "angee.integrate:external_account_owner",
})
OWNED = (("parties", "Party"), ("parties", "Folder"), ("parties", "Circle"), ("workflows", "Workflow"))


def _user(**options: object) -> models.ForeignKey:
    return models.ForeignKey(settings.AUTH_USER_MODEL, **options)


def _released(app_label: str, name: str, *fields: tuple[str, models.Field]) -> ModelState:
    return ModelState(
        app_label,
        name,
        [("id", models.AutoField(primary_key=True)), *fields],
        options={"db_table": f"cutover_{app_label}_{name.lower()}"},
    )


def released_state() -> ProjectState:
    """The released schema of every column the ownership cutovers read or change."""

    user_label, user_name = settings.AUTH_USER_MODEL.split(".")
    created_by = ("created_by", _user(null=True, blank=True, on_delete=models.SET_NULL, related_name="+"))
    state = ProjectState()
    for model_state in (
        _released(user_label, user_name),
        _released("agents", "AgentSession", ("owner", _user(on_delete=models.CASCADE, related_name="agent_sessions"))),
        _released(
            "parties",
            "Handle",
            ("owner", _user(null=True, blank=True, on_delete=models.SET_NULL, related_name="own_handles")),
        ),
        *(_released(app_label, name, created_by) for app_label, name in OWNED),
        _released("integrate", "ExternalAccount", created_by),
        *(ModelState.from_model(model) for model in (ContentType, RebacResource, Relationship, RelationshipRegistry)),
    ):
        state.add_model(model_state)
    return state


def _declared_cutovers(tmp_path: Path) -> RuntimeMigrations:
    return RuntimeMigrations((apps.get_app_config(label) for label in LABELS), runtime_dir=tmp_path, labels=LABELS)


def _upgraded(scheduled: tuple[ScheduledMigration, ...]) -> ProjectState:
    state = released_state()
    for entry in scheduled:
        state = entry.migration_class(entry.declaration["name"], entry.declaration["app_label"]).mutate_state(state)
    return state


def test_each_cutover_applies_once_from_the_released_schema_only(tmp_path: Path) -> None:
    """The guards select every declaration from the released schema and none afterwards or on a fresh stack."""

    cutovers = _declared_cutovers(tmp_path)
    scheduled = cutovers.schedule(released_state(), only=ORIGINS)
    assert {entry.origin for entry in scheduled} == ORIGINS
    assert {entry.round for entry in scheduled} == {0}

    assert cutovers.schedule(_upgraded(scheduled), only=ORIGINS) == ()
    assert cutovers.schedule(ProjectState(), only=ORIGINS) == ()


def test_cutovers_land_on_the_current_field_definitions(tmp_path: Path) -> None:
    """After the cutovers, makemigrations finds nothing left to change on these fields."""

    upgraded = _upgraded(_declared_cutovers(tmp_path).schedule(released_state(), only=ORIGINS))
    current = upgraded.clone()
    for (app_label, model_name, name), source in {
        ("agents", "agentsession", "user"): AgentSession._meta.get_field("user"),
        ("parties", "handle", "controller"): Handle._meta.get_field("controller"),
        **{
            (app_label, model_name.lower(), "owner"): OwnerMixin._meta.get_field("owner")
            for app_label, model_name in (*OWNED, ("integrate", "ExternalAccount"))
        },
    }.items():
        current.alter_field(app_label, model_name, name, source.clone(), preserve_default=True)
    graph = MigrationGraph()
    for label in LABELS:
        graph.add_node((label, "0001_initial"), migrations.Migration("0001_initial", label))
    assert MigrationAutodetector(upgraded, current).changes(graph=graph) == {}


def _columns(table: str) -> dict[str, bool]:
    with connection.cursor() as cursor:
        return {column.name: column.null_ok for column in connection.introspection.get_table_description(cursor, table)}


@pytest.mark.django_db(transaction=True)
def test_renames_keep_each_session_user_and_handle_controller() -> None:
    state = released_state()
    user, session, handle = (
        state.apps.get_model(label) for label in (settings.AUTH_USER_MODEL, "agents.AgentSession", "parties.Handle")
    )
    with model_tables((user, session, handle)):
        alice = user._base_manager.create()
        session._base_manager.create(owner_id=alice.pk)
        handle._base_manager.create(owner_id=alice.pk)
        handle._base_manager.create(owner_id=None)
        with connection.schema_editor() as editor:
            state = session_user.Migration("0002_session_user", "agents").apply(state, editor)
            state = handle_controller.Migration("0002_handle_controller", "parties").apply(state, editor)

        session, handle = state.apps.get_model("agents", "AgentSession"), state.apps.get_model("parties", "Handle")
        assert list(session._base_manager.values_list("user_id", flat=True)) == [alice.pk]
        assert list(handle._base_manager.order_by("pk").values_list("controller_id", flat=True)) == [alice.pk, None]
        assert _columns(session._meta.db_table) == {"id": False, "user_id": True}
        assert _columns(handle._meta.db_table) == {"id": False, "controller_id": True}


@pytest.mark.django_db(transaction=True)
def test_owner_columns_start_from_each_rows_creator_and_keep_a_set_owner() -> None:
    state = released_state()
    user = state.apps.get_model(settings.AUTH_USER_MODEL)
    owned = tuple(state.apps.get_model(app_label, name) for app_label, name in OWNED)
    with model_tables((user, *owned)):
        author, recipient = user._base_manager.create(), user._base_manager.create()
        for model in owned:
            model._base_manager.create(created_by_id=author.pk)
            model._base_manager.create(created_by_id=None)
        with connection.schema_editor() as editor:
            state = parties_owner_column.Migration("0002_owner_column", "parties").apply(state, editor)
            state = workflows_owner_column.Migration("0002_owner_column", "workflows").apply(state, editor)
        owned = tuple(state.apps.get_model(app_label, name) for app_label, name in OWNED)
        for model in owned:
            assert list(model._base_manager.order_by("pk").values_list("created_by_id", "owner_id")) == [
                (author.pk, author.pk), (None, None),
            ]
            model._base_manager.filter(created_by_id=author.pk).update(owner_id=recipient.pk)
        with connection.schema_editor() as editor:
            parties_owner_column.forwards(state.apps, editor)
            workflows_owner_column.forwards(state.apps, editor)
        for model in owned:
            assert list(model._base_manager.order_by("pk").values_list("owner_id", flat=True)) == [recipient.pk, None]


@pytest.mark.django_db(transaction=True)
def test_external_account_owner_tuples_become_the_owner_column(caplog: pytest.LogCaptureFixture) -> None:
    """Each store's owner tuple becomes the column; unresolvable ones leave the account unowned and are reported."""

    state = released_state()
    user, account = state.apps.get_model(settings.AUTH_USER_MODEL), state.apps.get_model("integrate", "ExternalAccount")
    resource, wide, registry = (
        state.apps.get_model("rebac", name) for name in ("RebacResource", "Relationship", "RelationshipRegistry")
    )

    def registry_tuple(resource_type: str, resource_id: object, relation: str, subject_id: object) -> None:
        resources = resource._base_manager
        registry._base_manager.create(
            resource_fk=resources.get_or_create(resource_type=resource_type, resource_id=str(resource_id))[0],
            relation=relation,
            subject_fk=resources.get_or_create(resource_type="auth/user", resource_id=str(subject_id))[0],
        )

    def wide_tuple(resource_type: str, resource_id: object, relation: str, subject_id: object, **extra: str) -> None:
        wide._base_manager.create(
            resource_type=resource_type,
            resource_id=str(resource_id),
            relation=relation,
            subject_type="auth/user",
            subject_id=str(subject_id),
            **extra,
        )

    with model_tables((user, account, resource, wide, registry)):
        alice, bob = user._base_manager.create(), user._base_manager.create()
        gone_user = bob.pk + 100
        by_registry, by_wide, ambiguous, dangling_user, creator_only = (
            account._base_manager.create(created_by_id=alice.pk) for _ in range(5)
        )
        gone_account = creator_only.pk + 100
        registry_tuple("integrate/external_account", by_registry.pk, "owner", alice.pk)
        wide_tuple("integrate/external_account", by_wide.pk, "owner", bob.pk)
        registry_tuple("integrate/external_account", ambiguous.pk, "owner", alice.pk)
        wide_tuple("integrate/external_account", ambiguous.pk, "owner", bob.pk)
        registry_tuple("integrate/external_account", dangling_user.pk, "owner", gone_user)
        registry_tuple("integrate/external_account", gone_account, "owner", alice.pk)
        # Kept: another relation, another resource type, and a subject set are not account owners.
        registry_tuple("integrate/external_account", by_registry.pk, "viewer", bob.pk)
        registry_tuple("integrate/integration", by_registry.pk, "owner", bob.pk)
        wide_tuple("integrate/external_account", by_wide.pk, "owner", alice.pk, optional_subject_relation="member")

        with caplog.at_level(logging.INFO, logger=external_account_owner.__name__):
            with connection.schema_editor() as editor:
                state = external_account_owner.Migration("0002_external_account_owner", "integrate").apply(
                    state, editor,
                )

        account = state.apps.get_model("integrate", "ExternalAccount")
        assert dict(account._base_manager.values_list("pk", "owner_id")) == {
            by_registry.pk: alice.pk,
            by_wide.pk: bob.pk,
            ambiguous.pk: None,
            dangling_user.pk: None,
            creator_only.pk: None,
        }
        assert sorted(
            registry._base_manager.values_list("resource_fk__resource_type", "relation", "subject_fk__resource_id")
        ) == [("integrate/external_account", "viewer", str(bob.pk)), ("integrate/integration", "owner", str(bob.pk))]
        assert list(wide._base_manager.values_list("relation", "optional_subject_relation")) == [("owner", "member")]

    (warning,) = (record for record in caplog.records if record.levelno == logging.WARNING)
    for account_id, user_ids in (
        (ambiguous.pk, f"{alice.pk}, {bob.pk}"),
        (dangling_user.pk, str(gone_user)),
        (gone_account, str(alice.pk)),
    ):
        assert f"{account_id} (users {user_ids})" in warning.getMessage()
    assert "2 account(s) owned, 6 tuple(s) deleted" in caplog.text
