"""Retire the superseded hash bases after the record-sync stores are retired.

applied_base owns comparison evidence. The old hashes are deliberately discarded
on both live links and snapshots. Reverse restores empty columns; historical hash
values do not round-trip. No current runtime writer or reader uses these columns.

The guard waits while ``RecordRevision`` is still in the state: the manifest orders
``fold_record_sync_history`` and ``retire_record_sync_stores`` first, and the
composer attaches this drop to the round after that retirement, so the documented
chain order (fold, retire stores, retire hash bases) holds on every upgrading database.
"""

from django.db import migrations
from django.db.migrations.state import ProjectState


def applies(state: ProjectState) -> bool:
    owners = [state.models.get(("integrate", name)) for name in ("recordlink", "historicalrecordlink")]
    if all(owner is None for owner in owners):
        return False
    if any(owner is None for owner in owners):
        return False
    fields = {"remote_base_hash", "local_base_hash"}
    present = [fields & owner.fields.keys() for owner in owners]
    if not any(present):
        return False
    if ("integrate", "recordrevision") in state.models:
        # retire_record_sync_stores is pending in this build; wait for its round.
        return False
    if all(names == fields for names in present) and all("applied_base" in owner.fields for owner in owners):
        return True
    raise ValueError("Record sync has a partial hash-base retirement.")


class Migration(migrations.Migration):
    dependencies: list[tuple[str, str]] = []
    operations = [
        migrations.RemoveField("recordlink", "remote_base_hash"),
        migrations.RemoveField("recordlink", "local_base_hash"),
        migrations.RemoveField("historicalrecordlink", "remote_base_hash"),
        migrations.RemoveField("historicalrecordlink", "local_base_hash"),
    ]
