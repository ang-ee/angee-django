"""Fold legacy revisions and quarantine into native history and retained state.

Revision identity/number/prior and original audit stamps live in the converted
history row's reserved metadata key, so reverse restores them exactly. Historical
remote versions were never recorded and stay blank; today's version is not a
historical fact. All failures, including resolved and null-link failures, survive.
Each old refusal gets an outbox snapshot; the latest open refusal owns the live
outbox, with the greatest open retry time. No old tables are dropped here.

Reverse reconstructs both legacy tables. New native observations/applications
collapse consecutive identical evidence into ordered revisions: pointer-only and
failure-only history writes have no legacy revision equivalent. The old schema
cannot express pinned history FKs, source attribution, pin policy, outbox pending/
error distinctions, changeset grouping, or original new-state slot IDs. Reverse
loses those facts and stream history when its new schema is removed. Legacy
revision/discrepancy IDs, ordering, payloads, mapping facts and closure survive.
An absent mapped projection becomes an empty object in the non-null legacy
revision column. Repeated new failures collapse on the old open-failure key.
"""

from copy import deepcopy
from datetime import datetime

import simple_history.models
from django.conf import settings
from django.db import migrations, models
from django.db.migrations.state import ProjectState
from django.utils import timezone
from rebac import SubjectRef

from angee.base.fields import StateField

FOLD = "_sync_history_fold"
REASON = "integrate/fold_record_sync_history"


def applies(state: ProjectState) -> bool:
    """Select the complete link cutover and exact pre-stream-state shape."""
    stream = state.models.get(("integrate", "syncstream"))
    link = state.models.get(("integrate", "recordlink"))
    if stream is None and link is None:
        return False
    history = state.models.get(("integrate", "historicalrecordlink"))
    stream_history = state.models.get(("integrate", "historicalsyncstream"))
    required = {"applied_base", "source_payload", "source_hash", "mapped_payload", "mapping_version",
                "dependency_digest", "applied_at", "asserted_by", "inbound", "outbox_state",
                "outbox_retry_at", "outbox_last_error", "pinned"}
    if stream is not None and link is not None and history is not None and required <= link.fields.keys():
        if "inbound" in stream.fields and stream_history is not None:
            expected = {name for op in Migration.operations if isinstance(op, migrations.CreateModel)
                        for name, _field in op.fields}
            if (expected <= stream_history.fields.keys() and link.fields["mapped_payload"].null
                    and history.fields["mapped_payload"].null):
                return False
        legacy = {"id", "created_at", "updated_at", "created_by", "updated_by", "integration", "key",
                  "partition", "kind", "direction", "generation", "phase", "cursor", "reconcile_state",
                  "config", "cursor_expires_at", "resync_required", "last_advanced_at", "last_reconciled_at",
                  "reconcile_interval", "absence_threshold", "tombstone_retention"}
        if set(stream.fields) == legacy and stream_history is None and all(
            ("integrate", name) in state.models for name in ("recordrevision", "syncdiscrepancy")
        ):
            return True
    raise ValueError("Record sync has a partial or unsupported fold schema; complete or reverse its history.")


def _snapshot(history, owner, db, changeset, date, **overrides):
    """Copy known operational columns; converted revision facts override them."""
    names = {field.attname for field in owner._meta.concrete_fields}
    values = {field.attname: getattr(owner, field.attname) for field in history._meta.concrete_fields
              if field.attname in names and not field.primary_key}
    values.update(overrides)
    return history._base_manager.using(db).create(
        **values, history_date=date, history_type="~", changeset_id=changeset.pk,
        history_change_reason=REASON,
    )


def _failure(row):
    data = {name: getattr(row, name) for name in (
        "kind", "code", "source_hash", "mapping_version", "details", "status", "attempts",
    )}
    data.update({name: value.isoformat() if value is not None else None
                 for name in ("retry_at", "resolved_at", "created_at", "updated_at")
                 for value in (getattr(row, name),)})
    data["legacy_id"] = row.pk
    data["legacy_link_id"], data["legacy_stream_id"] = row.link_id, row.stream_id
    data["legacy_created_by_id"], data["legacy_updated_by_id"] = row.created_by_id, row.updated_by_id
    return data


def forwards(apps, schema_editor):
    db = schema_editor.connection.alias
    link_model = apps.get_model("integrate", "RecordLink")
    stream_model = apps.get_model("integrate", "SyncStream")
    history = apps.get_model("integrate", "HistoricalRecordLink")
    stream_history = apps.get_model("integrate", "HistoricalSyncStream")
    revisions = apps.get_model("integrate", "RecordRevision")._base_manager.using(db)
    failures = apps.get_model("integrate", "SyncDiscrepancy")._base_manager.using(db)
    if not revisions.order_by().exists() and not failures.order_by().exists():
        return
    expected_revisions = revisions.order_by().count()
    converted_revisions = 0
    cs_model = apps.get_model(settings.ANGEE_CHANGESET_MODEL)
    cs = cs_model._base_manager.using(db).create(activity="system", change_reason=REASON, state="closed", outcome="success")
    counts = {"integrate.recordlink": 0, "integrate.syncstream": 0}
    now = timezone.now()
    for link in link_model._base_manager.using(db).order_by("pk").iterator():
        if FOLD in link.metadata or link.applied_base_id or history._base_manager.using(db).filter(id=link.pk).exists():
            raise ValueError(f"Link {link.pk} already has history/state; fold before enabling the new driver.")
        integration_id = stream_model._base_manager.using(db).get(pk=link.stream_id).integration_id
        link.asserted_by = str(SubjectRef.of("integrate/integration", str(integration_id)))
        latest, applied = None, None
        for revision in revisions.filter(link_id=link.pk).order_by("number").iterator():
            metadata = deepcopy(link.metadata)
            legacy = {field.attname: getattr(revision, field.attname) for field in revision._meta.concrete_fields}
            metadata[FOLD] = {"revision": {name: value.isoformat() if isinstance(value, datetime) else value
                                            for name, value in legacy.items()}}
            converted = _snapshot(
                history, link, db, cs, revision.created_at, metadata=metadata,
                source_payload=revision.source_payload, source_hash=revision.source_hash,
                mapping_version=revision.mapping_version, mapped_payload=revision.mapped_payload,
                dependency_digest=revision.dependency_digest, applied_at=revision.applied_at,
                applied_base_id=applied.pk if applied is not None else None,
                remote_version="", remote_base_hash=revision.source_hash if revision.applied_at else "",
                local_base_hash="", origin="",
            )
            counts["integrate.recordlink"] += 1
            converted_revisions += 1
            latest = revision
            if revision.applied_at is not None:
                applied = converted
        if latest is not None:
            link.source_payload, link.source_hash = latest.source_payload, latest.source_hash
        if applied is not None:
            link.applied_base_id = applied.pk
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
            _snapshot(history, link, db, cs, row.updated_at,
                      metadata={**link.metadata, FOLD: {"state": True}})
            counts["integrate.recordlink"] += 1
        if open_refusals:
            latest_refusal = open_refusals[-1]
            link.outbox_last_error = _failure(latest_refusal)
            link.outbox_state = "refused"
            link.outbox_retry_at = max((row.retry_at for row in open_refusals if row.retry_at is not None), default=None)
        values = {name: getattr(link, name) for name in (
            "asserted_by", "source_payload", "source_hash", "mapped_payload", "mapping_version", "dependency_digest",
            "applied_at", "applied_base_id", "inbound", "outbox_state", "outbox_retry_at", "outbox_last_error",
        )}
        link_model._base_manager.using(db).filter(pk=link.pk).update(**values)
        _snapshot(history, link, db, cs, now, metadata={**link.metadata, FOLD: {"state": True}})
        counts["integrate.recordlink"] += 1
    for stream in stream_model._base_manager.using(db).order_by("pk").iterator():
        rows = failures.filter(stream_id=stream.pk, link_id=None).order_by("pk")
        for row in rows.iterator():
            stream.inbound.append(_failure(row))
            _snapshot(stream_history, stream, db, cs, row.updated_at)
            counts["integrate.syncstream"] += 1
        if stream.inbound:
            stream_model._base_manager.using(db).filter(pk=stream.pk).update(inbound=stream.inbound)
    cs_model._base_manager.using(db).filter(pk=cs.pk).update(counts=counts, touched=sorted(name for name, n in counts.items() if n))
    if converted_revisions != expected_revisions:
        raise ValueError("Record revision folding did not preserve every legacy observation.")


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
    dependencies = [migrations.swappable_dependency(settings.AUTH_USER_MODEL),
                    migrations.swappable_dependency(settings.ANGEE_CHANGESET_MODEL)]
    operations = [
        migrations.AlterField("recordlink", "mapped_payload", models.JSONField(default=dict, blank=True, null=True)),
        migrations.AlterField("historicalrecordlink", "mapped_payload", models.JSONField(default=dict, blank=True, null=True)),
        migrations.AddField("syncstream", "inbound", models.JSONField(default=list, blank=True)),
        migrations.CreateModel(
            name="HistoricalSyncStream",
            fields=[
                ("id", models.BigIntegerField(auto_created=True, blank=True, db_index=True, verbose_name="ID")),
                ("created_at", models.DateTimeField(blank=True, db_index=True, editable=False)),
                ("updated_at", models.DateTimeField(blank=True, db_index=True, editable=False)),
                ("inbound", models.JSONField(default=list, blank=True)),
                ("key", models.CharField(max_length=160)),
                ("partition", models.CharField(max_length=255, blank=True)),
                ("kind", StateField(choices=[("event_feed", "Event feed"), ("record_replica", "Record replica")], max_length=14, db_index=True)),
                ("direction", StateField(choices=[("pull", "Pull"), ("push", "Push"), ("bidirectional", "Bidirectional")], max_length=13, db_index=True)),
                ("generation", models.PositiveIntegerField(default=1)),
                ("phase", StateField(choices=[("baseline", "Baseline"), ("delta", "Delta")], default="baseline", max_length=8, db_index=True)),
                ("cursor", models.JSONField(default=dict, blank=True)),
                ("reconcile_state", models.JSONField(default=dict, blank=True)),
                ("config", models.JSONField(default=dict, blank=True)),
                ("cursor_expires_at", models.DateTimeField(null=True, blank=True)),
                ("resync_required", models.BooleanField(default=False)),
                ("last_advanced_at", models.DateTimeField(null=True, blank=True)),
                ("last_reconciled_at", models.DateTimeField(null=True, blank=True)),
                ("reconcile_interval", models.DurationField(null=True, blank=True)),
                ("absence_threshold", models.PositiveIntegerField(default=2)),
                ("tombstone_retention", models.DurationField(null=True, blank=True)),
                ("history_id", models.AutoField(primary_key=True, serialize=False)),
                ("history_date", models.DateTimeField(db_index=True)),
                ("history_change_reason", models.TextField(null=True)),
                ("history_type", models.CharField(max_length=1, choices=[("+", "Created"), ("~", "Changed"), ("-", "Deleted")])),
                ("integration", models.ForeignKey("integrate.Integration", null=True, blank=True, db_constraint=False, on_delete=models.DO_NOTHING, related_name="+")),
                ("changeset", models.ForeignKey(settings.ANGEE_CHANGESET_MODEL, null=True, blank=True, on_delete=models.PROTECT, related_name="+")),
                ("created_by", models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, db_constraint=False, on_delete=models.DO_NOTHING, related_name="+")),
                ("updated_by", models.ForeignKey(settings.AUTH_USER_MODEL, null=True, blank=True, db_constraint=False, on_delete=models.DO_NOTHING, related_name="+")),
                ("history_user", models.ForeignKey(settings.AUTH_USER_MODEL, null=True, on_delete=models.SET_NULL, related_name="+")),
            ],
            options={"verbose_name": "historical sync stream", "verbose_name_plural": "historical sync streams",
                     "ordering": ("-history_date", "-history_id"), "get_latest_by": ("history_date", "history_id")},
            bases=(simple_history.models.HistoricalChanges, models.Model),
        ),
        migrations.RunPython(forwards, backwards),
    ]
