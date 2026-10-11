"""Retire CardDAV columns and MergeVeto only after the fold's retained evidence.

Each scalar aspect must have its fold-time snapshot. Directory locators, bytes,
versions and extension data must be retained on a targeting link before removal.
Generic MergeVeto pointers block instead of dangling. Its permission tuples leave
with the resource; the new pair reads and writes through both Party endpoints.
Reverse restores empty legacy columns/tables, not a competing provenance store.
"""

from django.conf import settings
from django.db import migrations, models
from rebac import system_context


def applies(state):
    party = state.models.get(("parties", "party"))
    return party is not None and "source_uid" in party.fields and ("parties", "partysameaspairstatement") in state.models


def forwards(apps, schema_editor):
    db = schema_editor.connection.alias
    with system_context(reason="retire.party_carriers"):
        for name, aspects in (("Party", ("identity", "contact")), ("Person", ("name", "dates")),
                              ("Organization", ("legal_identity",)), ("Address", ("address",)),
                              ("Relationship", ("relationship",))):
            rows = apps.get_model("parties", name)._base_manager.using(db).order_by()
            history = apps.get_model("parties", "Historical" + name + "Statement")._base_manager.using(db).order_by()
            for row in rows.order_by("pk").iterator():
                party = row if name == "Party" else apps.get_model("parties", "Party")._base_manager.using(db).get(
                    pk=row.party_id if name in ("Address", "Relationship") else row.pk,
                )
                directory = row.source == "carddav" if name == "Relationship" else bool(party.folder_id and party.source_uid)
                actor = apps.get_model(settings.AUTH_USER_MODEL)._base_manager.using(db).order_by().filter(
                    pk=row.updated_by_id or row.created_by_id, kind="person",
                ).exists()
                for aspect in aspects:
                    if (directory or actor) and not history.filter(
                        subject_id=row.pk, aspect=aspect, history_change_reason="fold.party_aspects",
                    ).exists():
                        raise ValueError(f"{name} {row.pk}: {aspect} has no retained fold snapshot")
                if name != "Party":
                    continue
                if not row.folder_id and not any((row.source_uid, row.source_etag, row.raw_vcard, row.extensions)):
                    continue
                folder = apps.get_model("parties", "Folder")._base_manager.using(db).filter(pk=row.folder_id).first()
                if folder is None or not row.source_uid:
                    raise ValueError(f"Party {row.pk}: directory enrollment has no retained stable key")
                links = apps.get_model("integrate", "RecordLink")._base_manager.using(db).filter(
                    stream__integration_id=folder.directory_id, stream__key="contacts", stream__partition=folder.source_href,
                    external_key=row.source_uid, target_object_id=str(row.pk), target_content_type__app_label="parties",
                    target_content_type__model__in=("party", "person", "organization"),
                ).order_by("-stream__generation", "pk")
                link = links.first()
                if link is None or (row.raw_vcard and link.source_payload.get("raw_vcard") != row.raw_vcard) or (
                    row.source_etag and link.remote_version != row.source_etag
                ) or (row.extensions and link.source_payload.get("extensions") != row.extensions):
                    raise ValueError(f"Party {row.pk}: directory locator/payload parity is unproven")
        vetoes = apps.get_model("parties", "MergeVeto")._base_manager.using(db).order_by()
        statements = apps.get_model("parties", "PartySameAsPairStatement")._base_manager.using(db).order_by()
        for veto in vetoes.order_by("pk").iterator():
            left, right = sorted((veto.party_a_id, veto.party_b_id))
            if not statements.filter(subject__left_id=left, subject__right_id=right, rank="preferred", source_kind="person",
                                     snak="no_value", superseded_id=None).exists():
                raise ValueError(f"MergeVeto {veto.pk}: anti-link parity is unproven")
        # Dropping copied legacy columns may not erase earlier wire evidence.
        # Versions with no proven retained observation stop this retirement.
        for name in ("HistoricalParty", "HistoricalPerson", "HistoricalOrganization"):
            history = apps.get_model("parties", name)._base_manager.using(db).order_by("history_id")
            for row in history.iterator():
                if not row.folder_id and not any((row.source_uid, row.source_etag, row.raw_vcard, row.extensions)):
                    continue
                folder = apps.get_model("parties", "Folder")._base_manager.using(db).filter(pk=row.folder_id).first()
                if folder is None or not row.source_uid:
                    raise ValueError(f"{name} {row.history_id}: historical directory identity is unproven")
                links = apps.get_model("integrate", "RecordLink")._base_manager.using(db).filter(
                    stream__integration_id=folder.directory_id, stream__key="contacts", stream__partition=folder.source_href,
                    external_key=row.source_uid, target_object_id=str(row.id), target_content_type__app_label="parties",
                    target_content_type__model__in=("party", "person", "organization"),
                )
                versions = apps.get_model("integrate", "HistoricalRecordLink")._base_manager.using(db).filter(id__in=links.values("pk"))
                if row.source_etag:
                    versions = versions.filter(remote_version=row.source_etag)
                if row.raw_vcard:
                    versions = versions.filter(source_payload__raw_vcard=row.raw_vcard)
                if row.extensions:
                    versions = versions.filter(source_payload__extensions=row.extensions)
                if not versions.exists():
                    raise ValueError(f"{name} {row.history_id}: wire/version evidence has no retained RecordLink snapshot")
        ct = apps.get_model("contenttypes", "ContentType")._base_manager.using(db).filter(app_label="parties", model="mergeveto").first()
        if ct is not None:
            for model in apps.get_models():
                if any(field.name == "history_id" for field in model._meta.fields):
                    continue
                for field in model._meta.fields:
                    if isinstance(field, models.ForeignKey) and field.remote_field.model._meta.label_lower == "contenttypes.contenttype":
                        if model._base_manager.using(db).filter(**{field.name: ct.pk}).exists():
                            raise ValueError(f"{model._meta.label}.{field.name}: retained MergeVeto reference blocks retirement")
        for name, resource, subject in (("Relationship", "resource_type", "subject_type"),
                                         ("RelationshipRegistry", "resource_fk__resource_type", "subject_fk__resource_type")):
            apps.get_model("rebac", name)._base_manager.using(db).filter(
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
        migrations.RemoveField(model_name="relationship", name="source"),
        migrations.DeleteModel(name="MergeVeto"),
    ]
