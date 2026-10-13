"""Retire converted record-sync tables after an exact evidence parity check.

The composer attaches this declaration after fold_record_sync_history. Forward
refuses partial conversion or changed legacy evidence before either DeleteModel;
the parity scan runs before the stream schema change, so no exclusive table lock is
held while it reads. Retained failures are indexed once as canonical JSON, so the
check is linear in the number of snapshots and failures.
Reverse recreates both tables from ordered link history and retained stream state.
Legacy revision/failure primary keys do not round-trip; payloads, mapping and closure facts survive.
New native evidence collapses consecutive identical observations and open failure
identities; the old schema cannot express pinned history FKs, source attribution,
pin policy, outbox pending/error distinctions, changeset grouping or state slot
IDs. Missing mapped projections become empty objects. Those distinctions are
lost when the new schema is later reversed; retained native history stays here.
Stream closure and snapshot intent have no legacy equivalents either.
"""

import json
from copy import deepcopy
from datetime import datetime

from django.db import migrations, models
from django.db.migrations.state import ProjectState

from angee.base.fields import StateField


def applies(state: ProjectState) -> bool:
    """Require the complete folded schema before removing both conversion inputs."""
    legacy = {name for name in ("recordrevision", "syncdiscrepancy") if ("integrate", name) in state.models}
    if not legacy:
        return False
    if len(legacy) != 2:
        raise ValueError("Record sync has a partially retired schema.")
    link = state.models.get(("integrate", "recordlink"))
    stream = state.models.get(("integrate", "syncstream"))
    history = state.models.get(("integrate", "historicalrecordlink"))
    # Earlier declarations may still be pending in the composer's fixed point.
    if history is None or stream is None or "inbound" not in stream.fields:
        return False
    required = {"applied_base", "source_payload", "source_hash", "mapped_payload", "mapping_version",
                "dependency_digest", "applied_at", "asserted_by", "inbound", "outbox_state",
                "outbox_retry_at", "outbox_last_error", "pinned"}
    if link is None or not required <= link.fields.keys() or not required <= history.fields.keys():
        raise ValueError("Record sync retirement requires the complete converted link schema.")
    if not link.fields["mapped_payload"].null or not history.fields["mapped_payload"].null:
        raise ValueError("Record sync retirement requires the completed history fold.")
    if "changeset" not in history.fields:
        raise ValueError("Record sync retirement requires attributed link history.")
    if any(name in stream.fields for name in ("state", "snapshot")):
        raise ValueError("Record sync retirement found partial stream lifecycle columns.")
    return True


def forwards(apps, schema_editor):
    """Prove every original row survives in history before either table is dropped."""
    db = schema_editor.connection.alias
    revisions = apps.get_model("integrate", "RecordRevision")
    failures = apps.get_model("integrate", "SyncDiscrepancy")
    history = apps.get_model("integrate", "HistoricalRecordLink")
    retained = set()

    def key(entry, link_id, stream_id):
        return json.dumps([entry, link_id, stream_id], sort_keys=True, separators=(",", ":"), default=str)

    def retain(owner, link_id, stream_id):
        entries = [*owner.inbound]
        outbox = getattr(owner, "outbox_last_error", {})
        if outbox:
            entries.append(outbox)
        retained.update(key(entry, link_id, stream_id) for entry in entries)

    for row in history._base_manager.using(db).order_by("history_id").iterator():
        retain(row, row.id, row.stream_id)
    for row in apps.get_model("integrate", "RecordLink")._base_manager.using(db).order_by("pk").iterator():
        retain(row, row.pk, row.stream_id)
    for row in apps.get_model("integrate", "SyncStream")._base_manager.using(db).order_by("pk").iterator():
        retain(row, None, row.pk)
    for row in revisions._base_manager.using(db).order_by("pk").iterator():
        evidence = {name: getattr(row, name) for name in (
            "source_payload", "source_hash", "mapping_version", "mapped_payload", "dependency_digest", "applied_at",
        )}
        if not history._base_manager.using(db).filter(
            id=row.link_id, history_date=row.created_at, history_change_reason="integrate/fold_record_sync_history",
            changeset_id__isnull=False, **evidence,
        ).exists():
            raise ValueError(f"Record sync retirement parity failed for revision {row.pk}.")
    for row in failures._base_manager.using(db).order_by("pk").iterator():
        expected = {name: getattr(row, name) for name in (
            "kind", "code", "source_hash", "mapping_version", "details", "status", "attempts",
        )}
        expected.update({name: value.isoformat() if value is not None else None
                         for name in ("retry_at", "resolved_at", "created_at", "updated_at")
                         for value in (getattr(row, name),)})
        expected.update(legacy_created_by_id=row.created_by_id, legacy_updated_by_id=row.updated_by_id)
        if key(expected, row.link_id, row.stream_id) not in retained:
            raise ValueError(f"Record sync retirement parity failed for failure {row.pk}.")
    # The protected comparison base must be an applied snapshot of its own link.
    links = apps.get_model("integrate", "RecordLink")._base_manager.using(db)
    for link in links.order_by("pk").iterator():
        applied = revisions._base_manager.using(db).filter(link_id=link.pk, applied_at__isnull=False).exists()
        if link.applied_base_id is None:
            if applied:
                raise ValueError(f"Record sync retirement found an unpinned applied link {link.pk}.")
        elif not history._base_manager.using(db).filter(
            pk=link.applied_base_id, id=link.pk, applied_at__isnull=False,
        ).exists():
            raise ValueError(f"Record sync retirement found an invalid applied base for link {link.pk}.")


def _restore(model, db, values):
    """Rebuild legacy natural identities without promising original primary keys."""
    values = deepcopy(values)
    if model._meta.model_name == "recordrevision" and values.get("mapped_payload") is None:
        values["mapped_payload"] = {}
    for name in ("created_at", "updated_at", "applied_at", "retry_at", "resolved_at"):
        if isinstance(values.get(name), str):
            values[name] = datetime.fromisoformat(values[name])
    if model._meta.model_name == "recordrevision":
        key = {name: values[name] for name in ("link_id", "number")}
        row, _ = model._base_manager.using(db).update_or_create(**key, defaults=values)
    else:
        key = {name: values[name] for name in (
            "stream_id", "link_id", "kind", "code", "source_hash", "mapping_version", "created_at",
        )}
        row = model._base_manager.using(db).filter(**key).order_by("pk").first()
        if row is None and values["status"] != "resolved":
            open_key = {name: value for name, value in key.items() if name != "created_at"}
            row = model._base_manager.using(db).filter(**open_key).exclude(status="resolved").order_by("pk").first()
        if row is None:
            row = model._base_manager.using(db).create(**values)
    model._base_manager.using(db).filter(pk=row.pk).update(**values)
    return row


def backwards(apps, schema_editor):
    db = schema_editor.connection.alias
    revisions = apps.get_model("integrate", "RecordRevision")
    failures = apps.get_model("integrate", "SyncDiscrepancy")
    history = apps.get_model("integrate", "HistoricalRecordLink")
    failure_states = {}

    def retain_failures(owner, link_id, stream_id):
        for slot, entry in enumerate(owner.inbound):
            failure_states[("inbound", link_id, stream_id, slot)] = (entry, link_id, stream_id)
        entry = getattr(owner, "outbox_last_error", {})
        if entry:
            failure_states[("outbox", link_id, entry["created_at"])] = (entry, link_id, stream_id)

    for link in apps.get_model("integrate", "RecordLink")._base_manager.using(db).order_by("pk").iterator():
        prior, previous = None, None
        rows = history._base_manager.using(db).filter(id=link.pk).order_by("history_date", "history_id")
        for row in rows.iterator():
            retain_failures(row, link.pk, link.stream_id)
            evidence = (row.source_payload, row.source_hash, row.mapped_payload, row.mapping_version,
                        row.dependency_digest, row.applied_at)
            if evidence != previous:
                prior = _restore(revisions, db, dict(
                    link_id=link.pk, number=1 if prior is None else prior.number + 1,
                    prior_id=prior.pk if prior is not None else None,
                    source_payload=row.source_payload, source_hash=row.source_hash, mapped_payload=row.mapped_payload,
                    mapping_version=row.mapping_version, dependency_digest=row.dependency_digest, applied_at=row.applied_at,
                    created_at=row.history_date, updated_at=row.history_date,
                    created_by_id=row.created_by_id, updated_by_id=row.updated_by_id,
                ))
            previous = evidence
        retain_failures(link, link.pk, link.stream_id)
    for row in apps.get_model("integrate", "SyncStream")._base_manager.using(db).order_by("pk").iterator():
        retain_failures(row, None, row.pk)
    for entry, link_id, stream_id in failure_states.values():
        values = {name: entry[name] for name in (
            "kind", "code", "source_hash", "mapping_version", "details", "status", "attempts",
            "retry_at", "resolved_at", "created_at", "updated_at",
        )}
        values.update(link_id=link_id, stream_id=stream_id,
                      created_by_id=entry.get("legacy_created_by_id"), updated_by_id=entry.get("legacy_updated_by_id"))
        _restore(failures, db, values)


class Migration(migrations.Migration):
    """The manifest orders conversion first; the composer attaches its exact leaf."""

    dependencies: list[tuple[str, str]] = []
    operations = [
        migrations.RunPython(forwards, backwards),
        migrations.AddField("syncstream", "snapshot", models.BooleanField(default=False)),
        migrations.AddField("syncstream", "state", StateField(
            choices=[("open", "Open"), ("closed", "Closed")], default="open", max_length=6, db_index=True,
        )),
        migrations.DeleteModel("SyncDiscrepancy"),
        migrations.DeleteModel("RecordRevision"),
    ]
