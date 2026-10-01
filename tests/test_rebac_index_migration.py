"""Framework contracts for the local permission index migration seam."""

from __future__ import annotations

from unittest.mock import patch

from django.db.migrations import Migration
from django.db.migrations.operations.special import RunPython
from rebac.index.program import program_errors
from rebac.schema import parse_zed

from angee.base.signals import rebuild_permission_index


def test_data_migration_rebuilds_only_a_ready_local_index() -> None:
    """A bypassing migration repairs published grants; initial sync owns an unready index."""

    migration = Migration("0001", "test")
    migration.operations = [RunPython(RunPython.noop)]
    with (
        patch("angee.base.signals.router.allow_migrate_model", return_value=True),
        patch("angee.base.signals.SchemaGeneration.objects.revision_pair") as revision_pair,
        patch("angee.base.signals.call_command") as command,
    ):
        revision_pair.return_value = ("revision", "revision")
        rebuild_permission_index(using="default", plan=[(migration, False)])
        command.assert_called_once_with("rebac", "index", "rebuild", database="default", verbosity=0)

        command.reset_mock()
        revision_pair.return_value = ("revision", None)
        rebuild_permission_index(using="default", plan=[(migration, False)])
        command.assert_not_called()

        revision_pair.reset_mock()
        rebuild_permission_index(using="default", plan=[])
        revision_pair.assert_not_called()


def test_recursive_parent_intersection_is_rejected() -> None:
    """E016 protects the task read path from a future parent intersection cycle."""

    schema = parse_zed("""
        definition auth/user {}
        definition projects/task {
            relation parent: projects/task
            relation viewer: auth/user
            permission read = parent->read & viewer
        }
    """)
    assert [issue.id for issue in program_errors(schema)] == ["rebac.E016"]
