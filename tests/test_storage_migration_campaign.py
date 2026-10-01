"""Historical storage ownership transition using Django's migration state."""


import pytest
from django.conf import settings
from django.db import connection, models
from django.db.migrations.state import ModelState, ProjectState

from angee.storage.runtime_migrations import owner_column
from tests.tables import model_tables


def legacy_state() -> ProjectState:
    """Describe only the columns consumed by this historical transition."""

    state = ProjectState()
    label, name = settings.AUTH_USER_MODEL.split(".")
    state.add_model(ModelState(label, name, [("id", models.AutoField(primary_key=True))],
                               options={"db_table": "campaign_legacy_user"}))
    for name in ("Drive", "File"):
        state.add_model(ModelState("storage", name, [
            ("id", models.AutoField(primary_key=True)),
            ("created_by", models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL)),
        ], options={"db_table": f"campaign_legacy_{name.lower()}"}))
    return state


@pytest.mark.parametrize("partial", ["missing_drive", "missing_file", "drive_owner", "file_owner"])
def test_owner_transition_refuses_partial_legacy_state(partial: str) -> None:
    state = legacy_state()
    if partial.startswith("missing_"):
        state.remove_model("storage", partial.removeprefix("missing_"))
    else:
        state.add_field("storage", partial.removesuffix("_owner"), "owner", models.IntegerField(null=True), True)
    with pytest.raises(ValueError, match="partial storage ownership transition"):
        owner_column.applies(state)


def test_owner_transition_applies_once_and_exposes_build_before_autodetect_requirement() -> None:
    state = legacy_state()
    assert owner_column.applies(state)
    for operation in owner_column.Migration.operations:
        operation.state_forwards("storage", state)
    assert not owner_column.applies(state)
    assert not owner_column.applies(ProjectState())


@pytest.mark.django_db(transaction=True)
def test_owner_backfill_preserves_populated_rows_null_authors_and_existing_transfers() -> None:
    state = legacy_state()
    for operation in owner_column.Migration.operations:
        operation.state_forwards("storage", state)
    user = state.apps.get_model(settings.AUTH_USER_MODEL)
    drive, file = (state.apps.get_model("storage", name) for name in ("Drive", "File"))
    with model_tables((user, drive, file)):
        author, recipient = user.objects.create(), user.objects.create()
        for model in (drive, file):
            model.objects.create(created_by_id=author.pk)
            model.objects.create(created_by_id=None)
            model.objects.create(created_by_id=author.pk, owner_id=recipient.pk)
        with connection.schema_editor() as editor:
            owner_column.forwards(state.apps, editor)
            owner_column.forwards(state.apps, editor)
        for model in (drive, file):
            assert list(model.objects.order_by("pk").values_list("created_by_id", "owner_id")) == [
                (author.pk, author.pk), (None, None), (author.pk, recipient.pk),
            ]
