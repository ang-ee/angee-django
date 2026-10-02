"""Replay the declared IAM transition against retained historical rows."""

import pytest
from django.contrib.auth import get_user_model
from django.db import IntegrityError, connection, models, transaction
from django.db.migrations.autodetector import MigrationAutodetector
from django.db.migrations.graph import MigrationGraph
from django.db.migrations.state import ModelState, ProjectState
from django.test.utils import CaptureQueriesContext

from angee.iam.runtime_migrations import normalize_person_emails as transition
from tests.tables import model_tables
from tests.test_runtime_migrations import isolated_upgrade_database as isolated_upgrade_database


@pytest.fixture
def legacy_email_state(transactional_db, isolated_upgrade_database):
    """Use the suite's isolated upgrade database and Django historical models."""

    model = ModelState.from_model(get_user_model())
    model.options["constraints"] = []
    model.managers = []
    model.options.pop("base_manager_name", None)
    model.options.pop("default_manager_name", None)
    model.bases = (models.Model,)
    state = ProjectState()
    state.add_model(model)
    with model_tables((state.apps.get_model("iam", "User"),)):
        yield state


def test_normalization_transition_preserves_rows_adds_constraint_and_is_idempotent(legacy_email_state):
    before = legacy_email_state
    historical_user = before.apps.get_model("iam", "User")
    raw = ["\u2003Ä@EXAMPLE.COM\u00a0", "\tİ@EXAMPLE.COM\n", "normalized@example.com", " \t", ""]
    for index, email in enumerate(raw):
        historical_user._base_manager.create(username=f"legacy-{index}", email=email, password="retained-hash")
    historical_user._base_manager.create(username="service", kind="service", email=raw[0], password="retained")
    rows_before = list(historical_user._base_manager.order_by("pk").values())
    assert transition.applies(before)
    migration = transition.Migration("normalize_person_emails", "iam")
    with connection.schema_editor() as editor:
        after = migration.apply(before.clone(), editor)
    upgraded = after.apps.get_model("iam", "User")
    actual = list(upgraded._base_manager.order_by("pk").values())
    expected_emails = ["ä@example.com", "i\u0307@example.com", "normalized@example.com", "", "", raw[0]]
    assert actual == [dict(row, email=email) for row, email in zip(rows_before, expected_emails, strict=True)]
    with connection.schema_editor() as editor, CaptureQueriesContext(connection) as captured:
        transition.forwards(after.apps, editor)
    assert not any(query["sql"].startswith("UPDATE") for query in captured)
    assert list(upgraded._base_manager.order_by("pk").values()) == actual
    with connection.cursor() as cursor:
        constraints = connection.introspection.get_constraints(cursor, upgraded._meta.db_table)
    assert constraints["iam_user_person_email_unique"]["unique"] is True
    with pytest.raises(IntegrityError), transaction.atomic():
        upgraded._base_manager.create(username="duplicate", email="ä@example.com", is_active=False, is_staff=True)
    expected = before.clone()
    expected.models["iam", "user"].options["constraints"] = [c.clone() for c in get_user_model()._meta.constraints]
    assert MigrationAutodetector(after, expected).changes(graph=MigrationGraph()) == {}


def test_migration_refuses_all_collisions_before_any_write_with_only_a_count(legacy_email_state):
    before = legacy_email_state
    user = before.apps.get_model("iam", "User")
    for index, email in enumerate([" First@EXAMPLE.COM ", "first@example.com", "İ@example.com", "i\u0307@EXAMPLE.COM"]):
        user._base_manager.create(username=f"collision-{index}", email=email)
    rows = list(user._base_manager.order_by("pk").values())
    with connection.schema_editor() as editor, CaptureQueriesContext(connection) as captured:
        with pytest.raises(RuntimeError) as caught:
            transition.Migration("normalize_person_emails", "iam").apply(before.clone(), editor)
    assert str(caught.value) == (
        "2 normalized person email addresses collide; resolve iam_email_collisions before migrating."
    )
    assert not any(q["sql"].startswith(("UPDATE", "INSERT", "DELETE", "CREATE")) for q in captured)
    assert list(user._base_manager.order_by("pk").values()) == rows
    with connection.cursor() as cursor:
        assert "iam_user_person_email_unique" not in connection.introspection.get_constraints(
            cursor, user._meta.db_table
        )


def test_normalization_migration_is_complete_once_history_carries_the_constraint(legacy_email_state):
    assert transition.applies(ProjectState()) is False
    assert transition.applies(legacy_email_state) is True
    fresh = ProjectState()
    fresh.add_model(ModelState.from_model(get_user_model()))
    assert transition.applies(fresh) is False
