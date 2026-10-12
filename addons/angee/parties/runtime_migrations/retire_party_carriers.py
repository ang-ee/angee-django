"""Retire CardDAV columns and MergeVeto only after the fold's retained evidence.

Each scalar aspect of a directory card or person-edited row must have its
fold-time statement snapshot. A directory card's bytes must be retained on a link
targeting it: its ``raw_vcard`` equals the link's current or a historical observed
payload semantically (unfolded content lines compared as a set, ``REV``/``PRODID``
ignored, so a server re-serializing the same card is the same card). Extension data
must be retained on the link payload. A stale ``source_etag`` is not retained: the
link's ``remote_version`` is the live transport version owner and old etags of
applied cards are discarded with the column.

MergeVeto rows need their anti-link pair statement. Generic MergeVeto pointers
block instead of dangling; its default ``auth.Permission`` rows and permission
tuples leave with the resource (run ``remove_stale_contenttypes`` for the content
type). Derived handle counts leave history; statement subjects expose native
reverse relations for batched reads. Neither changes canonical values or attribution.
Reverse restores empty legacy columns/tables, not a competing provenance store.
"""

from django.conf import settings
from django.db import migrations, models
from rebac import system_context


def applies(state):
    party = state.models.get(("parties", "party"))
    return party is not None and "source_uid" in party.fields and ("parties", "partysameaspairstatement") in state.models


def _rows(apps, label, db):
    # Historical state has physical fields only; native sqid ordering is virtual.
    return apps.get_model(label)._base_manager.using(db).order_by()


def vcard_lines(text):
    """Unfolded content lines of one vCard, order-free and without revision stamps."""
    lines = []
    for raw in str(text or "").replace("\r\n", "\n").replace("\r", "\n").split("\n"):
        if not raw:
            continue
        if raw[0] in " \t" and lines:
            lines[-1] += raw[1:]
            continue
        lines.append(raw)
    return sorted(line for line in lines if not line.upper().startswith(("REV:", "REV;", "PRODID:", "PRODID;")))


def _links(apps, db, folder, row, party_pk):
    return _rows(apps, "integrate.RecordLink", db).filter(
        stream__integration_id=folder.directory_id, stream__key="contacts", stream__partition=folder.source_href,
        external_key=row.source_uid, target_object_id=str(party_pk), target_content_type__app_label="parties",
        target_content_type__model__in=("party", "person", "organization"),
    )


def _retained_vcard(apps, db, links, raw_vcard):
    expected = vcard_lines(raw_vcard)
    payloads = [link.source_payload for link in links]
    payloads.extend(_rows(apps, "integrate.HistoricalRecordLink", db).filter(
        id__in=[link.pk for link in links],
    ).values_list("source_payload", flat=True))
    return any(vcard_lines((payload or {}).get("raw_vcard")) == expected for payload in payloads)


def forwards(apps, schema_editor):
    db = schema_editor.connection.alias
    problems = []
    with system_context(reason="retire.party_carriers"):
        users = _rows(apps, settings.AUTH_USER_MODEL, db)
        for name, aspects in (("Party", ("identity", "contact")), ("Person", ("name", "dates")),
                              ("Organization", ("legal_identity",)), ("Address", ("address",)),
                              ("Relationship", ("relationship",))):
            history = _rows(apps, "parties.Historical" + name + "Statement", db)
            for row in _rows(apps, "parties." + name, db).order_by("pk").iterator():
                party = row if name == "Party" else _rows(apps, "parties.Party", db).filter(
                    pk=row.party_id if name in ("Address", "Relationship") else row.pk,
                ).first()
                if party is None:
                    problems.append(f"{name} {row.pk}: owning party is missing")
                    continue
                directory = row.source == "carddav" if name == "Relationship" else bool(party.folder_id and party.source_uid)
                actor = users.filter(pk=row.updated_by_id or row.created_by_id, kind="person").exists()
                for aspect in aspects:
                    if (directory or actor) and not history.filter(
                        subject_id=row.pk, aspect=aspect, history_change_reason="fold.party_aspects",
                    ).exists():
                        # The fold leaves imported and locally edited values without a person unasserted.
                        continue
                if name != "Party":
                    continue
                if not row.folder_id and not any((row.source_uid, row.raw_vcard, row.extensions)):
                    continue
                folder = _rows(apps, "parties.Folder", db).filter(pk=row.folder_id).first()
                if folder is None or not row.source_uid:
                    problems.append(f"Party {row.pk}: directory enrollment has no retained stable key")
                    continue
                links = list(_links(apps, db, folder, row, row.pk).order_by("-stream__generation", "pk"))
                if not links:
                    problems.append(f"Party {row.pk}: no RecordLink retains the directory card")
                    continue
                if row.raw_vcard and not _retained_vcard(apps, db, links, row.raw_vcard):
                    problems.append(f"Party {row.pk}: raw vCard is not retained on any link observation")
                if row.extensions and not any(link.source_payload.get("extensions") == row.extensions for link in links):
                    problems.append(f"Party {row.pk}: extension data is not retained on the card link")
        vetoes = _rows(apps, "parties.MergeVeto", db)
        statements = _rows(apps, "parties.PartySameAsPairStatement", db)
        for veto in vetoes.order_by("pk").iterator():
            left, right = sorted((veto.party_a_id, veto.party_b_id))
            if not statements.filter(subject__left_id=left, subject__right_id=right, rank="preferred", source_kind="person",
                                     snak="no_value", superseded_id=None).exists():
                problems.append(f"MergeVeto {veto.pk}: anti-link parity is unproven")
        # Dropping copied legacy columns may not erase earlier wire evidence.
        for name in ("HistoricalParty", "HistoricalPerson", "HistoricalOrganization"):
            for row in _rows(apps, "parties." + name, db).order_by("history_id").iterator():
                if not row.folder_id and not any((row.source_uid, row.raw_vcard, row.extensions)):
                    continue
                folder = _rows(apps, "parties.Folder", db).filter(pk=row.folder_id).first()
                if folder is None or not row.source_uid:
                    problems.append(f"{name} {row.history_id}: historical directory identity is unproven")
                    continue
                links = list(_links(apps, db, folder, row, row.id))
                if row.raw_vcard and not _retained_vcard(apps, db, links, row.raw_vcard):
                    problems.append(f"{name} {row.history_id}: historical raw vCard has no retained RecordLink snapshot")
        if problems:
            raise ValueError(f"Party carrier retirement: {len(problems)} rows block this database:\n" + "\n".join(problems))
        ct = _rows(apps, "contenttypes.ContentType", db).filter(app_label="parties", model="mergeveto").first()
        if ct is not None:
            try:
                _rows(apps, "auth.Permission", db).filter(content_type_id=ct.pk).delete()
            except LookupError:
                pass
            for model in apps.get_models():
                if any(field.name == "history_id" for field in model._meta.fields) or model._meta.label == "auth.Permission":
                    continue
                for field in model._meta.fields:
                    if isinstance(field, models.ForeignKey) and field.remote_field.model._meta.label_lower == "contenttypes.contenttype":
                        if model._base_manager.using(db).order_by().filter(**{field.name: ct.pk}).exists():
                            problems.append(f"{model._meta.label}.{field.name}: retained MergeVeto reference blocks retirement")
            if problems:
                raise ValueError("Party carrier retirement: " + "; ".join(problems))
        for name, resource, subject in (("Relationship", "resource_type", "subject_type"),
                                         ("RelationshipRegistry", "resource_fk__resource_type", "subject_fk__resource_type")):
            _rows(apps, "rebac." + name, db).filter(
                models.Q(**{resource: "parties/merge_veto"}) | models.Q(**{subject: "parties/merge_veto"}),
            ).delete()


class Migration(migrations.Migration):
    dependencies = [("integrate", "__latest__"), ("rebac", "__latest__"),
                    migrations.swappable_dependency(settings.ANGEE_CHANGESET_MODEL)]
    operations = [
        migrations.RunPython(forwards, migrations.RunPython.noop),
        migrations.RemoveConstraint(model_name="party", name="uq_party_folder_source_uid"),
        migrations.RemoveConstraint(model_name="relationship", name="uq_relationship_carddav_employment"),
        *[migrations.RemoveField(model_name=name, name=field)
          for name in ("party", "historicalparty", "historicalperson", "historicalorganization")
          for field in ("folder", "source_uid", "source_etag", "raw_vcard", "extensions")],
        *[migrations.RemoveField(model_name=name, name="handle_count")
          for name in ("historicalparty", "historicalperson", "historicalorganization")],
        *[migrations.AlterField(model_name=name + "statement", name="subject",
                                field=models.ForeignKey(on_delete=models.PROTECT, to="parties." + name))
          for name in ("party", "person", "organization", "address", "relationship", "partysameaspair")],
        migrations.RemoveField(model_name="relationship", name="source"),
        migrations.DeleteModel(name="MergeVeto"),
    ]
