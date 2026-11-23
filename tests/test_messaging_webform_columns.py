"""Which plans messaging holds back: a removal of a channel's form columns ahead of the move.

Only the plan is judged here, as ``migrate`` hands it over; what the channels
hold, and the run itself, are judged with the move in
``tests/test_webforms_messaging_upgrade.py``.
"""

from __future__ import annotations

import pytest
from django.db import migrations, models
from django.db.migrations.operations.base import Operation

from angee.messaging.webform_columns import removals_ahead_of_the_move
from angee.messaging.webforms import is_move


def _migration(name: str, *operations: Operation, app: str = "messaging", move: bool = False) -> migrations.Migration:
    migration = migrations.Migration(name, app)
    migration.operations = list(operations)
    if move:
        migration.moves_webform_channels = True
    return migration


def _remove(column: str = "slug", model: str = "channel") -> Operation:
    return migrations.RemoveField(model, column)


_OTHER = _migration(
    "0010_another_app", migrations.AddField("form", "rank", models.IntegerField(default=0)), app="webforms",
)
_MOVE = _migration("0006_the_move", migrations.RunPython(migrations.RunPython.noop), _remove(), move=True)


@pytest.mark.parametrize(("plan", "held_back"), [
    pytest.param([_migration("0005_remove_channel_slug", _remove())], ["0005_remove_channel_slug"], id="alone"),
    pytest.param([_migration("0099_anything", _remove())], ["0099_anything"], id="under-any-name"),
    pytest.param(
        [_OTHER, _migration("0005_remove_channel_slug", _remove()), _MOVE], ["0005_remove_channel_slug"],
        id="before-the-move-among-other-apps",
    ),
    pytest.param(
        [_migration("0001_squashed_0005", migrations.AddField("thread", "rank", models.IntegerField(default=0)),
                    _remove())],
        ["0001_squashed_0005"], id="squashed-with-other-operations",
    ),
    pytest.param(
        [_migration("0005_remove", _remove("is_published", model="CHANNEL"))], ["0005_remove"],
        id="model-named-in-capitals",
    ),
    pytest.param(
        [_migration("0006_an_unmarked_copy", migrations.RunPython(migrations.RunPython.noop), _remove())],
        ["0006_an_unmarked_copy"], id="a-copy-of-the-template-without-its-mark",
    ),
    pytest.param(
        [_migration("0005_separate", migrations.SeparateDatabaseAndState(
            database_operations=[_remove()], state_operations=[_remove()],
        ))],
        ["0005_separate"], id="among-separate-database-operations",
    ),
    pytest.param([_OTHER, _MOVE, _migration("0007_later", _remove())], [], id="after-the-move"),
    pytest.param([_MOVE], [], id="the-move-itself"),
    pytest.param([_migration("0005_remove", _remove("name"))], [], id="another-column"),
    pytest.param([_migration("0005_remove", _remove(), app="webforms")], [], id="another-apps-channel"),
    pytest.param(
        [_migration("0005_separate", migrations.SeparateDatabaseAndState(state_operations=[_remove()]))], [],
        id="removed-from-the-state-only",
    ),
])
def test_a_removal_of_a_form_column_is_held_back_unless_the_move_comes_first(
    plan: list[migrations.Migration], held_back: list[str],
) -> None:
    assert [migration.name for migration in removals_ahead_of_the_move(plan)] == held_back


@pytest.mark.parametrize("operations", [
    pytest.param([migrations.RunSQL("ALTER TABLE messaging_channel DROP COLUMN slug")], id="dropped-by-sql"),
    pytest.param(
        [migrations.RenameField("channel", "slug", "old_slug"), _remove("old_slug")], id="renamed-then-removed",
    ),
])
def test_a_column_that_leaves_without_a_removal_naming_it_is_not_seen(operations: list[Operation]) -> None:
    """The stated limit of what is read from a plan: neither shape says in its operations which column leaves."""

    assert removals_ahead_of_the_move([_migration("0005_by_hand", *operations)]) == []


def test_the_move_is_known_by_what_its_migration_declares() -> None:
    assert (is_move(_MOVE), is_move(_migration("0006_webform_channels_to_forms", _remove()))) == (True, False)
