"""The participants-to-recipients template, applied through Django's executor to the previous release's rows.

The previous release's recipient rows are ``messaging.Participant`` rows: the test puts
that model, with its fields and constraints, in place of ``Recipient`` in a migration
state derived from the installed models and creates its table, so the template's
callables run on historical models. Messages, threads and handles are the installed
models' rows. Every name and address is invented. The drop guard judges a copy of the
template in a project's own history (``tests.project_history``), as ``migrate`` and
``provision`` judge it.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import pytest
from django.apps import apps
from django.core.management.base import CommandError
from django.db import connection, migrations, models
from django.db.migrations.autodetector import MigrationAutodetector
from django.db.migrations.executor import MigrationExecutor
from django.db.migrations.graph import MigrationGraph
from django.db.migrations.recorder import MigrationRecorder
from django.db.migrations.state import ModelState, ProjectState
from django.utils import timezone
from django_compose.migrations import DropGuardAutodetector
from rebac import system_context

from angee.base.fields.state import StateField
from angee.messaging.compose_migrations.messaging import participants_to_recipients as template
from angee.messaging.testing.models import Handle, Message, Recipient, Thread
from tests.project_history import INITIAL, project_history

pytestmark = pytest.mark.django_db(transaction=True)

_NAME = "0099_participants_to_recipients"
_COPY = "0002_participants_to_recipients"
_PARTICIPANTS, _RECIPIENTS = "messaging_participant", "messaging_recipient"


def _previous_release_state() -> ProjectState:
    """The installed migration state with the previous release's ``Participant`` in place of ``Recipient``."""

    state = ProjectState.from_apps(apps)
    recipient = state.models["messaging", "recipient"]
    fields = {name: field.clone() for name, field in recipient.fields.items()}
    fields.update(
        message=models.ForeignKey(
            "messaging.message", null=True, blank=True, on_delete=models.CASCADE, related_name="participants",
        ),
        thread=models.ForeignKey(
            "messaging.thread", null=True, blank=True, on_delete=models.CASCADE, related_name="participants",
        ),
        handle=models.ForeignKey("parties.handle", on_delete=models.CASCADE, related_name="participations"),
        role=StateField(
            choices=[("from", "From"), ("to", "To"), ("cc", "Cc"), ("bcc", "Bcc")],
            db_index=True, default="to", max_length=4,
        ),
        joined_at=models.DateTimeField(null=True, blank=True),
        left_at=models.DateTimeField(null=True, blank=True),
    )
    # The composed host's table carries the default name the template renames.
    options = {key: value for key, value in recipient.options.items() if key != "db_table"}
    options["constraints"] = [
        models.UniqueConstraint(
            fields=("message", "handle", "role"), condition=models.Q(message__isnull=False),
            name="uq_participant_message_handle_role",
        ),
        models.UniqueConstraint(
            fields=("thread", "handle", "role"), condition=models.Q(message__isnull=True),
            name="uq_participant_thread_handle_role",
        ),
    ]
    state.remove_model("messaging", "recipient")
    state.add_model(ModelState(
        "messaging", "Participant", list(fields.items()), options, bases=recipient.bases,
        managers=list(recipient.managers),
    ))
    return state


def _history() -> MigrationGraph:
    """A project that has authored its messaging history; the autodetector writes nothing for an app without one."""

    graph = MigrationGraph()
    graph.add_node(("messaging", "0001_initial"), migrations.Migration("0001_initial", "messaging"))
    return graph


def _tables() -> set[str]:
    return set(connection.introspection.table_names())


@contextmanager
def _previous_release() -> Iterator[ProjectState]:
    """Create the previous release's participant table and yield its state; drop whichever table is left.

    PostgreSQL names a constraint once per schema, and the bare host's own recipient
    table already holds the name the template gives the renamed table: that table
    yields it for the block and takes it back after.
    """

    state = _previous_release_state()
    yields = connection.vendor == "postgresql"
    with connection.schema_editor() as editor:
        editor.create_model(state.apps.get_model("messaging", "Participant"))
        for constraint in Recipient._meta.constraints if yields else ():
            editor.remove_constraint(Recipient, constraint)
    try:
        yield state
    finally:
        left = _tables()
        with connection.schema_editor() as editor:
            if _RECIPIENTS in left:
                editor.delete_model(_after(state).apps.get_model("messaging", "Recipient"))
            if _PARTICIPANTS in left:
                editor.delete_model(state.apps.get_model("messaging", "Participant"))
            for constraint in Recipient._meta.constraints if yields else ():
                editor.add_constraint(Recipient, constraint)
        MigrationRecorder(connection).record_unapplied("messaging", _NAME)


def _after(state: ProjectState) -> ProjectState:
    return _migration().mutate_state(state, preserve=True)


def _migration() -> migrations.Migration:
    return template.Migration(_NAME, "messaging")


def _rows(state: ProjectState, model: str) -> set[tuple[Any, ...]]:
    rows = state.apps.get_model("messaging", model)._base_manager.order_by()
    return set(rows.values_list("pk", "message_id", "thread_id", "handle_id", "role"))


def _columns(table: str) -> dict[str, bool]:
    with connection.cursor() as cursor:
        return {column.name: column.null_ok for column in connection.introspection.get_table_description(cursor, table)}


def _constraints(table: str) -> set[str]:
    with connection.cursor() as cursor:
        return set(connection.introspection.get_constraints(cursor, table))


def _people(*names: str) -> dict[str, Any]:
    return {name: Handle._base_manager.create(platform="email", value=f"{name}@example.test") for name in names}


def _message(thread: Any, key: str, sender: Any | None) -> Any:
    return Message._base_manager.create(thread=thread, sender=sender, external_id=key, platform="email")


def _counted(caplog: pytest.LogCaptureFixture) -> list[Any]:
    """The counts the template logged, read from each record's arguments."""

    return [record.args for record in caplog.records if record.name == template.__name__]


def _participant(state: ProjectState, message: Any | None, thread: Any, handle: Any, role: str = "to") -> Any:
    """A previous release's row, written through its historical model by the installed rows' keys."""

    return state.apps.get_model("messaging", "Participant")._base_manager.create(
        message_id=None if message is None else message.pk, thread_id=thread.pk, handle_id=handle.pk, role=role,
    )


def test_the_template_renames_the_rows_moves_their_senders_and_reverses(caplog: pytest.LogCaptureFixture) -> None:
    """Each step in order on the previous release's rows; the result is the installed model's state; the reverse."""

    executor = MigrationExecutor(connection)
    with system_context(reason="test recipients upgrade"), _previous_release() as before:
        people = _people("ada", "bob", "cat", "dan", "eve", "eve-old", "fay")
        Handle._base_manager.filter(pk=people["eve-old"].pk).update(merged_into=people["eve"])
        thread = Thread._base_manager.create(platform="email")
        sent = _message(thread, "sent", people["ada"])
        unsent = _message(thread, "unsent", None)
        twice = _message(thread, "twice", None)
        other = _message(thread, "other", people["ada"])
        merged = _message(thread, "merged", people["eve"])

        def row(message: Any | None, name: str, role: str) -> Any:
            return _participant(before, message, thread, people[name], role)

        recipients = {
            row(sent, "bob", "to"), row(sent, "cat", "cc"), row(unsent, "bob", "to"), row(twice, "ada", "bcc"),
        }
        for message, name in (
            (sent, "ada"), (unsent, "dan"), (twice, "eve"), (twice, "fay"), (other, "cat"), (merged, "eve-old"),
        ):
            row(message, name, "from")
        expected = {(row.pk, row.message_id, thread.pk, row.handle_id, row.role) for row in recipients}

        with caplog.at_level(logging.WARNING, logger=template.__name__):
            executor.apply_migration(before.clone(), _migration())
        after = _after(before)

        # 1. The table is renamed with its rows: the recipients keep their primary keys.
        assert _PARTICIPANTS not in _tables()
        assert _rows(after, "Recipient") == expected
        # 2. A message without a sender takes its ``from`` row's handle, the first of two.
        senders = dict(Message._base_manager.order_by().values_list("external_id", "sender_id"))
        assert senders == {
            "sent": people["ada"].pk, "unsent": people["dan"].pk, "twice": people["eve"].pk,
            "other": people["ada"].pk, "merged": people["eve"].pk,
        }
        # 3. Two ``from`` rows named someone else: Fay beside Eve, Cat for Ada. Eve's old
        # address is Eve's merge group.
        assert _counted(caplog) == [(2,)]
        # 4. No ``from`` row is left (the expected rows are exactly the recipients).
        # 6. The columns, the constraint and the model state are the installed model's.
        columns = _columns(_RECIPIENTS)
        assert not {"joined_at", "left_at"} & set(columns)
        assert columns["message_id"] is False
        assert {"uq_recipient_message_handle_role"} <= _constraints(_RECIPIENTS)
        assert not {"uq_participant_message_handle_role", "uq_participant_thread_handle_role"} & _constraints(
            _RECIPIENTS,
        )
        (written,) = MigrationAutodetector(after, ProjectState.from_apps(apps)).changes(graph=_history())["messaging"]
        # The bare test host names its own table; a composed host keeps the renamed default. The template keeps its
        # bytes, so the host's next native migration moves the constraint to the mailbox's.
        assert [
            (type(operation).__name__, getattr(operation, "constraint", operation).name.lower())
            for operation in written.operations
        ] == [
            ("RemoveConstraint", "uq_recipient_message_handle_role"),
            ("AddConstraint", "uq_recipient_message_handle_role_address"),
            ("AlterModelTable", "recipient"),
        ]

        executor.unapply_migration(before.clone(), _migration())

        assert _RECIPIENTS not in _tables()
        assert _rows(before, "Participant") == expected
        assert {"joined_at", "left_at"} <= set(_columns(_PARTICIPANTS))
        assert {"uq_participant_message_handle_role", "uq_participant_thread_handle_role"} <= _constraints(
            _PARTICIPANTS,
        )
        # The reverse writes no ``from`` row back and keeps the senders step 2 set.
        assert Message._base_manager.get(pk=unsent.pk).sender_id == people["dan"].pk


def test_a_row_without_a_message_refuses_the_template_until_the_stated_remedy_runs(
    caplog: pytest.LogCaptureFixture,
) -> None:
    """The refusal comes first and changes nothing; it states the rows without a message, of any role, and its remedy.

    The remedy deletes exactly the rows it counts, and the template then applies.
    """

    executor = MigrationExecutor(connection)
    with system_context(reason="test recipients upgrade refusal"), _previous_release() as before:
        people = _people("ada", "bob", "cat")
        thread = Thread._base_manager.create(platform="email")
        unsent = _message(thread, "unsent", None)
        other = _message(thread, "other", people["bob"])
        for message, name, role in (
            (unsent, "ada", "from"), (unsent, "bob", "to"), (other, "cat", "from"), (None, "cat", "to"),
            (None, "ada", "from"),
        ):
            _participant(before, message, thread, people[name], role)
        kept = _rows(before, "Participant")

        with caplog.at_level(logging.WARNING, logger=template.__name__), pytest.raises(RuntimeError) as refused:
            executor.apply_migration(before.clone(), _migration())

        # Nothing changed and nothing was counted: the refusal runs before every other step.
        assert _counted(caplog) == []
        assert _RECIPIENTS not in _tables()
        assert _rows(before, "Participant") == kept
        assert Message._base_manager.get(pk=unsent.pk).sender_id is None
        assert not MigrationRecorder(connection).applied_migrations().get(("messaging", _NAME))

        statement = template.remedy(_PARTICIPANTS)
        assert statement in str(refused.value)
        with connection.cursor() as cursor:
            cursor.execute(statement)
            deleted = cursor.rowcount
        # The refusal states the count of the rows its remedy deletes: both rows without a message.
        assert deleted == sum(message is None for _, message, *_ in kept) == 2
        assert str(deleted) in str(refused.value).split()
        executor.apply_migration(before.clone(), _migration())

        assert _rows(_after(before), "Recipient") == {
            (pk, message, thread_id, handle, role) for pk, message, thread_id, handle, role in kept
            if message is not None and role != "from"
        }
        assert Message._base_manager.get(pk=unsent.pk).sender_id == people["ada"].pk


def test_the_drop_guard_refuses_an_autodetected_delete_and_create_of_participant_rows() -> None:
    """``makemigrations`` without the template: a delete and a create, refused while the table holds rows.

    The guard is the composer's (``DropGuardAutodetector``): it judges what it
    autodetects against the database the command runs on, so on an empty table the
    same delete and create is written, and the upgrade text says what that costs.
    """

    graph = _history()
    with system_context(reason="test recipients drop guard"), _previous_release() as before:
        people = _people("ada", "bob")
        thread = Thread._base_manager.create(platform="email")
        _participant(before, _message(thread, "sent", people["ada"]), thread, people["bob"])

        with pytest.raises(CommandError):
            DropGuardAutodetector(before, ProjectState.from_apps(apps)).changes(graph=graph)

        before.apps.get_model("messaging", "Participant")._base_manager.order_by().delete()
        (written,) = DropGuardAutodetector(before, ProjectState.from_apps(apps)).changes(graph=graph)["messaging"]
        assert {(type(operation).__name__, getattr(operation, "name", "")) for operation in written.operations} >= {
            ("DeleteModel", "Participant"), ("CreateModel", "Recipient"),
        }


def _dated_rows(state: ProjectState) -> set[int]:
    """A message's recipients, one of them holding the date it joined, and its sender's row; return the recipients."""

    people = _people("ada", "bob", "cat")
    thread = Thread._base_manager.create(platform="email")
    sent = _message(thread, "sent", people["ada"])
    joined = _participant(state, sent, thread, people["bob"])
    copied = _participant(state, sent, thread, people["cat"], "cc")
    _participant(state, sent, thread, people["ada"], "from")
    participants = state.apps.get_model("messaging", "Participant")._base_manager
    participants.filter(pk=joined.pk).update(joined_at=timezone.now())
    return {joined.pk, copied.pk}


def test_migrate_applies_a_copy_of_the_template_whose_rows_hold_a_joined_date(tmp_path: Path) -> None:
    """The template declares the drop of ``joined_at`` and ``left_at``: the drop guard lets the values go."""

    with (
        system_context(reason="test recipients upgrade under the drop guard"),
        _previous_release() as before,
        project_history(tmp_path) as history,
    ):
        recipients = _dated_rows(before)
        history.start(before, "messaging", "parties")
        history.copy("messaging", template, _COPY, {"0000_messaging_leaf": INITIAL})

        history.migrate("messaging", _COPY)

        assert history.applied("messaging", _COPY)
        assert _PARTICIPANTS not in _tables()
        assert not {"joined_at", "left_at"} & set(_columns(_RECIPIENTS))
        assert {pk for pk, *_ in _rows(_after(before), "Recipient")} == recipients


def test_the_drop_guard_refuses_a_copy_that_drops_a_joined_date_undeclared(tmp_path: Path) -> None:
    """A copy taken before the template declared its drop is refused while a row holds a date; nothing changes."""

    with (
        system_context(reason="test recipients upgrade under the drop guard"),
        _previous_release() as before,
        project_history(tmp_path) as history,
    ):
        _dated_rows(before)
        kept = _rows(before, "Participant")
        history.start(before, "messaging", "parties")
        history.copy("messaging", template, _COPY, {"0000_messaging_leaf": INITIAL}, declarations=False)

        with pytest.raises(CommandError) as refused:
            history.migrate("messaging", _COPY)

        assert not history.applied("messaging", _COPY)
        assert _rows(before, "Participant") == kept
        assert {"joined_at", "left_at"} <= set(_columns(_PARTICIPANTS))
    said = str(refused.value)
    assert said.startswith(f"unsafe migration drop: messaging.{_COPY} (Remove field joined_at from recipient): ")
    assert f"{_PARTICIPANTS}.joined_at holds data in 1 rows on 'default'" in said
    assert "left_at" not in said  # It holds nothing.
    assert "Nothing was migrated" in said and "IntendedRemoveField" in said
