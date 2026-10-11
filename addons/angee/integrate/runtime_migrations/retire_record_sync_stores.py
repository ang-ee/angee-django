"""Retire converted record-sync tables after an exact evidence parity check.

The composer attaches this declaration after fold_record_sync_history. Forward
refuses partial conversion or changed legacy evidence before either DeleteModel.
Reverse recreates both tables and reconstructs them from link/stream history.
Original folded IDs, ordering, payloads, mapping and closure facts survive.
New native evidence collapses consecutive identical observations and open failure
identities; the old schema cannot express pinned history FKs, source attribution,
pin policy, outbox pending/error distinctions, changeset grouping or state slot
IDs. Missing mapped projections become empty objects. Those distinctions are
lost when the new schema is later reversed; retained native history stays here.
Stream closure and snapshot intent have no legacy equivalents either.
"""

from copy import deepcopy
from datetime import datetime

from django.db import migrations, models
from django.db.migrations.state import ProjectState

from angee.base.fields import StateField

FOLD = "_sync_history_fold"


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
    stream_history = state.models.get(("integrate", "historicalsyncstream"))
    # Earlier declarations may still be pending in the composer's fixed point.
    if history is None or stream_history is None or stream is None or "inbound" not in stream.fields:
        return False
    required = {"applied_base", "source_payload", "source_hash", "mapped_payload", "mapping_version",
                "dependency_digest", "applied_at", "asserted_by", "inbound", "outbox_state",
                "outbox_retry_at", "outbox_last_error", "pinned"}
    if link is None or not required <= link.fields.keys() or not required <= history.fields.keys():
        raise ValueError("Record sync retirement requires the complete converted link schema.")
    if not link.fields["mapped_payload"].null or not history.fields["mapped_payload"].null:
        raise ValueError("Record sync retirement requires the completed history fold.")
    if "changeset" not in history.fields or not {"inbound", "changeset"} <= stream_history.fields.keys():
        raise ValueError("Record sync retirement requires attributed link and stream history.")
    if any(name in owner.fields for owner in (stream, stream_history) for name in ("state", "snapshot")):
        raise ValueError("Record sync retirement found partial stream lifecycle columns.")
    return True


def forwards(apps, schema_editor):
    """Prove every original row survives in history before either table is dropped."""
    db = schema_editor.connection.alias
    revisions = apps.get_model("integrate", "RecordRevision")
    failures = apps.get_model("integrate", "SyncDiscrepancy")
    history = apps.get_model("integrate", "HistoricalRecordLink")
    converted = {}
    retained = {}

    def retain(owner):
        entries = [*owner.inbound]
        outbox = getattr(owner, "outbox_last_error", {})
        if outbox:
            entries.append(outbox)
        for entry in entries:
            if "legacy_id" in entry:
                retained.setdefault(entry["legacy_id"], []).append(entry)

    for row in history._base_manager.using(db).order_by("history_id").iterator():
        values = row.metadata.get(FOLD, {}).get("revision")
        if values is not None:
            if values["id"] in converted or row.changeset_id is None:
                raise ValueError(f"Record sync retirement found invalid converted revision {values['id']}.")
            evidence = {name: getattr(row, name) for name in (
                "source_payload", "source_hash", "mapping_version", "mapped_payload", "dependency_digest", "applied_at",
            )}
            evidence = {name: value.isoformat() if isinstance(value, datetime) else value
                        for name, value in evidence.items()}
            converted[values["id"]] = (row.id, row.history_date.isoformat(), values, evidence)
        retain(row)
    for name in ("RecordLink", "HistoricalSyncStream", "SyncStream"):
        for row in apps.get_model("integrate", name)._base_manager.using(db).order_by("pk").iterator():
            retain(row)
    for row in revisions._base_manager.using(db).order_by("pk").iterator():
        expected = {field.attname: getattr(row, field.attname) for field in row._meta.concrete_fields}
        expected = {name: value.isoformat() if isinstance(value, datetime) else value
                    for name, value in expected.items()}
        evidence = {name: expected[name] for name in (
            "source_payload", "source_hash", "mapping_version", "mapped_payload", "dependency_digest", "applied_at",
        )}
        if converted.get(row.pk) != (row.link_id, row.created_at.isoformat(), expected, evidence):
            raise ValueError(f"Record sync retirement parity failed for revision {row.pk}.")
    for row in failures._base_manager.using(db).order_by("pk").iterator():
        expected = {name: getattr(row, name) for name in (
            "kind", "code", "source_hash", "mapping_version", "details", "status", "attempts",
        )}
        expected.update({name: value.isoformat() if value is not None else None
                         for name in ("retry_at", "resolved_at", "created_at", "updated_at")
                         for value in (getattr(row, name),)})
        expected.update(legacy_id=row.pk, legacy_link_id=row.link_id, legacy_stream_id=row.stream_id,
                        legacy_created_by_id=row.created_by_id, legacy_updated_by_id=row.updated_by_id)
        if expected not in retained.get(row.pk, ()):
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
    """Restore original audit dates after native auto_now/auto_now_add saves."""
    values = deepcopy(values)
    if model._meta.model_name == "recordrevision" and values.get("mapped_payload") is None:
        values["mapped_payload"] = {}
    pk = values.pop("id", None)
    for name in ("created_at", "updated_at", "applied_at", "retry_at", "resolved_at"):
        if isinstance(values.get(name), str):
            values[name] = datetime.fromisoformat(values[name])
    row, _ = model._base_manager.using(db).update_or_create(pk=pk, defaults=values) if pk is not None else (
        model._base_manager.using(db).create(**values), True,
    )
    model._base_manager.using(db).filter(pk=row.pk).update(**values)
    return row


def backwards(apps, schema_editor):
    db = schema_editor.connection.alias
    revisions = apps.get_model("integrate", "RecordRevision")
    discrepancies = apps.get_model("integrate", "SyncDiscrepancy")
    history = apps.get_model("integrate", "HistoricalRecordLink")
    failure_states = {}

    def retain_failures(owner, link_id, stream_id):
        for slot, entry in enumerate(owner.inbound):
            key = ("legacy", entry["legacy_id"]) if "legacy_id" in entry else ("inbound", link_id, stream_id, slot)
            failure_states[key] = (entry, link_id, stream_id)
        entry = getattr(owner, "outbox_last_error", {})
        if entry:
            key = ("legacy", entry["legacy_id"]) if "legacy_id" in entry else ("outbox", link_id, entry["created_at"])
            failure_states[key] = (entry, link_id, stream_id)

    for link in apps.get_model("integrate", "RecordLink")._base_manager.using(db).order_by("pk").iterator():
        prior = revisions._base_manager.using(db).filter(link_id=link.pk).order_by("-number").first()
        previous = None
        rows = history._base_manager.using(db).filter(id=link.pk).order_by("history_date", "history_id")
        # Restore the original predecessor chain first, in original number order.
        converted = [row.metadata[FOLD]["revision"] for row in rows if "revision" in row.metadata.get(FOLD, {})]
        for values in sorted(converted, key=lambda value: value["number"]):
            _restore(revisions, db, values)
        prior = revisions._base_manager.using(db).filter(link_id=link.pk).order_by("-number").first()
        for row in rows:
            retain_failures(row, link.pk, link.stream_id)
            evidence = (row.source_payload, row.source_hash, row.mapped_payload, row.mapping_version,
                        row.dependency_digest, row.applied_at)
            if FOLD in row.metadata:
                previous = evidence
                continue
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
    stream_history = apps.get_model("integrate", "HistoricalSyncStream")
    for row in stream_history._base_manager.using(db).order_by("history_date", "history_id").iterator():
        retain_failures(row, None, row.id)
    for row in apps.get_model("integrate", "SyncStream")._base_manager.using(db).order_by("pk").iterator():
        retain_failures(row, None, row.pk)
    for entry, link_id, stream_id in failure_states.values():
        values = {name: entry[name] for name in (
            "kind", "code", "source_hash", "mapping_version", "details", "status", "attempts",
            "retry_at", "resolved_at", "created_at", "updated_at",
        )}
        values.update(id=entry.get("legacy_id"), link_id=entry.get("legacy_link_id", link_id),
                      stream_id=entry.get("legacy_stream_id", stream_id),
                      created_by_id=entry.get("legacy_created_by_id"), updated_by_id=entry.get("legacy_updated_by_id"))
        if values["id"] is None and values["status"] != "resolved":
            key = {name: values[name] for name in (
                "stream_id", "link_id", "kind", "code", "source_hash", "mapping_version",
            )}
            old = discrepancies._base_manager.using(db).filter(**key).exclude(status="resolved").order_by("pk").first()
            if old is not None:
                values["id"] = old.pk
        _restore(discrepancies, db, values)


class Migration(migrations.Migration):
    """The manifest orders conversion first; the composer attaches its exact leaf."""

    dependencies: list[tuple[str, str]] = []
    operations = [
        migrations.AddField("syncstream", "snapshot", models.BooleanField(default=False)),
        migrations.AddField("historicalsyncstream", "snapshot", models.BooleanField(default=False)),
        migrations.AddField("syncstream", "state", StateField(
            choices=[("open", "Open"), ("closed", "Closed")], default="open", max_length=6, db_index=True,
        )),
        migrations.AddField("historicalsyncstream", "state", StateField(
            choices=[("open", "Open"), ("closed", "Closed")], default="open", max_length=6, db_index=True,
        )),
        migrations.RunPython(forwards, backwards),
        migrations.DeleteModel("SyncDiscrepancy"),
        migrations.DeleteModel("RecordRevision"),
    ]
