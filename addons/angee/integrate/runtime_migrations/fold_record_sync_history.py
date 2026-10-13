"""Fold legacy observations into link history and failures into retained state.

Schema changes are declared separately in record_sync_state. Pre-existing native
history rows remain later evidence; only a set applied_base marks converted state.
Every link receives its integration attribution and one converted snapshot, links
without legacy observations included.
The first converted snapshot is '+'. Historical remote versions were not stored
and stay blank. Fold-time operational columns (target, parent, status, verification,
absence, pin policy, metadata and audit stamps) are copied from the link; only the
revision's source/mapped payloads, hashes, mapping/dependency facts and applied_at
are historical facts. Unlinked failures belong to the stream's inbound state.

Reverse rebuilds ordered revisions from history, collapsing consecutive equal
evidence, and failures from retained slots/outbox snapshots. Legacy revision and
failure primary keys do not round-trip. The old schema cannot express source
attribution, pinned history FKs, pin policy, changesets, outbox pending/error states
or slot identities. A null mapped projection becomes {} in the legacy schema;
the separate null alter admits local-deletion write-back evidence.
"""

from copy import deepcopy
from datetime import datetime

from django.conf import settings
from django.db import migrations
from django.db.migrations.state import ProjectState
from django.utils import timezone
from rebac import SubjectRef

REASON = "integrate/fold_record_sync_history"


def applies(state: ProjectState) -> bool:
    """Fold only after the separate state declaration, while legacy inputs remain."""
    if not all(("integrate", name) in state.models for name in ("recordrevision", "syncdiscrepancy")):
        return False
    stream = state.models.get(("integrate", "syncstream"))
    history = state.models.get(("integrate", "historicalrecordlink"))
    if stream is None or history is None or "inbound" not in stream.fields:
        return False
    return state.models["integrate", "recordlink"].fields["mapped_payload"].null


def _snapshot(history, owner, changeset, date, **overrides):
    """Copy known operational columns; converted revision facts override them."""
    names = {field.attname for field in owner._meta.concrete_fields}
    values = {field.attname: getattr(owner, field.attname) for field in history._meta.concrete_fields
              if field.attname in names and not field.primary_key}
    values.update(overrides)
    return history(
        **values, history_date=date, changeset_id=changeset.pk,
        history_change_reason=REASON,
    )


def _failure(row):
    data = {name: getattr(row, name) for name in (
        "kind", "code", "source_hash", "mapping_version", "details", "status", "attempts",
    )}
    data.update({name: value.isoformat() if value is not None else None
                 for name in ("retry_at", "resolved_at", "created_at", "updated_at")
                 for value in (getattr(row, name),)})
    data["legacy_created_by_id"], data["legacy_updated_by_id"] = row.created_by_id, row.updated_by_id
    return data


def forwards(apps, schema_editor):
    db = schema_editor.connection.alias
    link_model = apps.get_model("integrate", "RecordLink")
    stream_model = apps.get_model("integrate", "SyncStream")
    history = apps.get_model("integrate", "HistoricalRecordLink")
    revisions = apps.get_model("integrate", "RecordRevision")._base_manager.using(db)
    failures = apps.get_model("integrate", "SyncDiscrepancy")._base_manager.using(db)
    if not link_model._base_manager.using(db).order_by().exists() and not failures.order_by().exists():
        return
    expected_revisions = revisions.order_by().count()
    converted_revisions = 0
    cs_model = apps.get_model(settings.ANGEE_CHANGESET_MODEL)
    cs = cs_model._base_manager.using(db).create(activity="system", change_reason=REASON, state="closed", outcome="success")
    counts = {"integrate.recordlink": 0}
    streams = {row.pk: row for row in stream_model._base_manager.using(db).order_by("pk")}
    now = timezone.now()
    for link in link_model._base_manager.using(db).order_by("pk").iterator():
        if link.applied_base_id:
            raise ValueError(f"Link {link.pk} already has converted application state; fold before enabling the new driver.")
        integration_id = streams[link.stream_id].integration_id
        link.asserted_by = str(SubjectRef.of("integrate/integration", str(integration_id)))
        latest, applied = None, None
        snapshots, bases = [], []
        for revision in revisions.filter(link_id=link.pk).order_by("number").iterator():
            converted = _snapshot(
                history, link, cs, revision.created_at, history_type="+" if not snapshots else "~",
                source_payload=revision.source_payload, source_hash=revision.source_hash,
                mapping_version=revision.mapping_version, mapped_payload=revision.mapped_payload,
                dependency_digest=revision.dependency_digest, applied_at=revision.applied_at,
                applied_base_id=None, remote_version="", origin="",
            )
            snapshots.append(converted)
            bases.append(applied)
            converted_revisions += 1
            latest = revision
            if revision.applied_at is not None:
                applied = converted
        if latest is not None:
            link.source_payload, link.source_hash = latest.source_payload, latest.source_hash
        if applied is not None:
            for name in ("mapped_payload", "mapping_version", "dependency_digest", "applied_at"):
                setattr(link, name, getattr(applied, name))
        rows = list(failures.filter(link_id=link.pk).order_by("pk"))
        open_refusals = [row for row in rows if row.kind == "remote_rejected" and row.status != "resolved"]
        for row in rows:
            data = _failure(row)
            if row.kind == "remote_rejected":
                link.outbox_last_error = data
                link.outbox_state = None if row.status == "resolved" else "refused"
                link.outbox_retry_at = row.retry_at
            else:
                link.inbound.append(data)
            snapshots.append(_snapshot(history, link, cs, row.updated_at,
                                       history_type="+" if not snapshots else "~"))
            bases.append(applied)
        if open_refusals:
            latest_refusal = open_refusals[-1]
            link.outbox_last_error = _failure(latest_refusal)
            link.outbox_state = "refused"
            link.outbox_retry_at = max((row.retry_at for row in open_refusals if row.retry_at is not None), default=None)
        snapshots.append(_snapshot(history, link, cs, now, history_type="+" if not snapshots else "~"))
        bases.append(applied)
        history._base_manager.using(db).bulk_create(snapshots)
        for snapshot, base in zip(snapshots, bases, strict=True):
            snapshot.applied_base_id = base.pk if base is not None else None
        history._base_manager.using(db).bulk_update(snapshots, ["applied_base"])
        link.applied_base_id = applied.pk if applied is not None else None
        counts["integrate.recordlink"] += len(snapshots)
        values = {name: getattr(link, name) for name in (
            "asserted_by", "source_payload", "source_hash", "mapped_payload", "mapping_version", "dependency_digest",
            "applied_at", "applied_base_id", "inbound", "outbox_state", "outbox_retry_at", "outbox_last_error",
        )}
        link_model._base_manager.using(db).filter(pk=link.pk).update(**values)
    for stream in streams.values():
        stream.inbound.extend(_failure(row) for row in failures.filter(stream_id=stream.pk, link_id=None).order_by("pk"))
        if stream.inbound:
            stream_model._base_manager.using(db).filter(pk=stream.pk).update(inbound=stream.inbound)
    cs_model._base_manager.using(db).filter(pk=cs.pk).update(counts=counts, touched=sorted(name for name, n in counts.items() if n))
    if converted_revisions != expected_revisions:
        raise ValueError("Record revision folding did not preserve every legacy observation.")


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
    dependencies = [migrations.swappable_dependency(settings.AUTH_USER_MODEL),
                    migrations.swappable_dependency(settings.ANGEE_CHANGESET_MODEL)]
    operations = [migrations.RunPython(forwards, backwards)]
