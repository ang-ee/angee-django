"""Fold proven identity, address, relationship and anti-link facts.

No REBAC policy is evaluated. Historical base managers write under one migration
changeset. Directory attribution requires its folder, integration, stable key and
targeted RecordLink, including retained wire payload/version. Manual facts require
a persisted person; imports without an unambiguous retained statement source block
with the row named. Existing occupied keys block rather than overwrite evidence.

Reverse is a no-op: provenance stays retained alongside the legacy columns until
the separate retirement. New history records the fold time, not invented dates.
"""

from django.apps import apps as live_apps
from django.conf import settings
from django.db import migrations
from django.utils import timezone
from rebac import SubjectRef, app_settings, system_context
from rebac.resources import subject_id_attr

from angee.base.identity import public_id_for
from angee.base.serialization import json_safe

REASON = "fold.party_aspects"
ASPECTS = {
    "Party": {"identity": ("display_name",), "contact": ("notes", "avatar")},
    "Person": {"name": ("name_prefix", "given_name", "additional_name", "family_name", "name_suffix", "nickname"),
               "dates": ("birthday", "anniversary")},
    "Organization": {"legal_identity": ("legal_name", "domain")},
    "Address": {"address": ("label", "po_box", "extended", "street", "city", "region", "postal_code", "country", "latitude", "longitude")},
    "Relationship": {"relationship": ("other_name", "title", "started_at", "ended_at", "notes")},
}


def applies(state):
    party = state.models.get(("parties", "party"))
    return party is not None and "source_uid" in party.fields and ("parties", "partysameaspairstatement") in state.models


def rows(apps, db, label):
    return apps.get_model(label)._base_manager.using(db).order_by()


def blocked(row, reason):
    raise ValueError(f"{row._meta.label} {row.pk}: {reason}")


def person_source(apps, db, row):
    actor_id = row.updated_by_id or row.created_by_id
    user = rows(apps, db, settings.AUTH_USER_MODEL).filter(pk=actor_id, kind="person", is_active=True).first()
    if user is None:
        blocked(row, "manual assertion has no proven person principal")
    identity = live_apps.get_model(settings.AUTH_USER_MODEL)
    return str(SubjectRef.of(app_settings.REBAC_USER_TYPE, str(getattr(user, subject_id_attr(identity)))))


def directory_link(apps, db, row, party):
    folder = rows(apps, db, "parties.Folder").filter(pk=party.folder_id).first()
    if folder is None or not folder.directory_id or not party.source_uid:
        blocked(row, "directory fact has no stable folder/key attribution")
    integration = rows(apps, db, "integrate.Integration").filter(pk=folder.directory_id).first()
    if integration is None:
        blocked(row, "directory principal is missing")
    links = rows(apps, db, "integrate.RecordLink").filter(
        stream__integration_id=integration.pk, stream__key="contacts", stream__partition=folder.source_href,
        external_key=party.source_uid, target_object_id=str(party.pk),
        target_content_type__app_label="parties", target_content_type__model__in=("party", "person", "organization"),
    ).order_by("-stream__generation", "pk")
    link = links.first()
    if link is None:
        blocked(row, "no observed RecordLink targets the retained directory identity")
    identity = live_apps.get_model("integrate.Integration")
    source = str(SubjectRef.of("integrate/integration", str(getattr(integration, subject_id_attr(identity)))))
    if link.asserted_by and link.asserted_by != source:
        blocked(row, "link principal contradicts the directory")
    if party.raw_vcard and link.source_payload.get("raw_vcard") != party.raw_vcard:
        blocked(row, "wire payload does not match retained RecordLink evidence")
    if party.source_etag and link.remote_version != party.source_etag:
        blocked(row, "transport version does not match retained RecordLink evidence")
    return source, link


def write(apps, db, cs, name, subject_id, aspect, source, kind, values, *, link=None, preferred=False, snak="value"):
    statements = rows(apps, db, "parties." + name + "Statement")
    key = dict(subject_id=subject_id, aspect=aspect, asserted_by=source, superseded_id=None)
    if statements.filter(**key).exists():
        raise ValueError(f"{name} {subject_id}: occupied {aspect} statement key {source}")
    withdrawn = link is not None and link.status in ("withdrawn", "tombstone")
    statement = statements.create(**key, **values, source_kind=kind, rank="preferred" if preferred else "normal",
                                  snak=snak, status="withdrawn" if withdrawn else "asserted", applied=not withdrawn, confidence=1.0,
                                  link_id=link.pk if link else None, changeset_id=cs.pk,
                                  evidence=[{"model": "integrate.RecordLink" if link else "parties." + name,
                                             "id": public_id_for(live_apps.get_model("integrate.RecordLink"), link.pk)
                                             if link else public_id_for(live_apps.get_model("parties." + name), subject_id)}])
    snapshot = {field.attname: getattr(statement, field.attname) for field in statement._meta.concrete_fields}
    rows(apps, db, "parties.Historical" + name + "Statement").create(
        **snapshot, history_type="+", history_date=timezone.now(), history_change_reason=REASON,
    )
    label = statement._meta.label_lower
    cs.counts[label] = cs.counts.get(label, 0) + 1
    cs.touched.append(label)


def prove_values(apps, db, row, name, fields, link):
    if link.applied_base_id is None:
        blocked(row, "directory assertion has no retained application snapshot")
    base = rows(apps, db, "integrate.HistoricalRecordLink").get(history_id=link.applied_base_id)
    payload = base.mapped_payload
    values = {field: json_safe(getattr(row, row._meta.get_field(field).attname)) for field in fields}
    if name == "Address":
        projected = {key: value for key, value in values.items() if key not in ("latitude", "longitude")}
        if any(values[key] is not None for key in ("latitude", "longitude")) or projected not in payload.get("addresses", []):
            blocked(row, "address values cannot be attributed to the application snapshot")
    elif name == "Relationship":
        expected = {"other_name": payload.get("organization", ""), "title": payload.get("title", ""),
                    "notes": payload.get("role", ""), "started_at": None, "ended_at": None}
        if values != expected:
            blocked(row, "relationship values cannot be attributed to the application snapshot")
    else:
        for field, value in values.items():
            expected = payload.get(field)
            if field == "display_name":
                expected = expected or payload.get("family_name") or "Unknown"
            elif field == "avatar":
                expected = None
                if value is not None:
                    file = rows(apps, db, "storage.File").filter(pk=value).first()
                    if file is None or (payload.get("photo") or {}).get("hash") != file.content_hash:
                        blocked(row, "avatar origin does not match the application snapshot")
                    expected = value
            if value != expected:
                blocked(row, f"{field} cannot be attributed to the application snapshot")


def forwards(apps, schema_editor):
    db = schema_editor.connection.alias
    with system_context(reason=REASON):
        cs = rows(apps, db, settings.ANGEE_CHANGESET_MODEL).create(
            activity="system", change_reason=REASON, state="closed", outcome="success",
        )
        for name, aspects in ASPECTS.items():
            for row in rows(apps, db, "parties." + name).order_by("pk").iterator():
                party = row if name == "Party" else rows(apps, db, "parties.Party").get(pk=row.party_id if name in ("Address", "Relationship") else row.pk)
                directory = bool(party.folder_id and party.source_uid)
                if name == "Relationship":
                    directory = row.source == "carddav"
                    if row.source not in ("manual", "carddav"):
                        blocked(row, f"legacy relationship source {row.source!r} has no proven principal")
                if directory:
                    source, link = directory_link(apps, db, row, party)
                    kind = "directory"
                else:
                    if name in ("Party", "Person", "Organization") and rows(apps, db, "parties.PartyStatement").filter(
                        subject_id=party.pk, aspect="handle", superseded_id=None,
                    ).exclude(source_kind="person").exists():
                        blocked(row, "non-person origins have no retained per-aspect attribution")
                    source, link, kind = person_source(apps, db, row), None, "person"
                for aspect, fields in aspects.items():
                    if directory:
                        prove_values(apps, db, row, name, fields, link)
                    values = {row._meta.get_field(field).attname: getattr(row, row._meta.get_field(field).attname) for field in fields}
                    write(apps, db, cs, name, row.pk, aspect, source, kind, values, link=link)
                if name == "Party" and directory:
                    # Payload is the evidence owner; retain old extension data there.
                    if party.extensions:
                        payload = dict(link.source_payload)
                        if "extensions" in payload and payload["extensions"] != party.extensions:
                            blocked(row, "extension payload contradicts retained evidence")
                        payload["extensions"] = party.extensions
                        if link.source_payload != payload:
                            link.source_payload = payload
                            snapshot = {field.attname: getattr(link, field.attname) for field in link._meta.concrete_fields}
                            rows(apps, db, "integrate.HistoricalRecordLink").create(
                                **snapshot, changeset_id=cs.pk, history_type="~", history_date=timezone.now(), history_change_reason=REASON,
                            )
                            cs.touched.append("integrate.recordlink")
                            cs.counts["integrate.recordlink"] = cs.counts.get("integrate.recordlink", 0) + 1
                        rows(apps, db, "integrate.RecordLink").filter(pk=link.pk).update(source_payload=payload)
                    claims = rows(apps, db, "parties.PartyStatement").filter(
                        subject_id=row.pk, aspect="handle", source_kind="directory", asserted_by=source, link_id=None,
                    )
                    for claim in claims.order_by("pk"):
                        claim.link_id, claim.changeset_id = link.pk, cs.pk
                        claims.filter(pk=claim.pk).update(link_id=link.pk, changeset_id=cs.pk)
                        snapshot = {field.attname: getattr(claim, field.attname) for field in claim._meta.concrete_fields}
                        rows(apps, db, "parties.HistoricalPartyStatement").create(
                            **snapshot, history_type="~", history_date=timezone.now(), history_change_reason=REASON,
                        )
                        cs.touched.append("parties.partystatement")
                        cs.counts["parties.partystatement"] = cs.counts.get("parties.partystatement", 0) + 1
        try:
            vetoes = rows(apps, db, "parties.MergeVeto")
        except LookupError:
            vetoes = ()
        for veto in vetoes:
            left, right = sorted((veto.party_a_id, veto.party_b_id))
            if left == right or rows(apps, db, "parties.Party").filter(pk__in=(left, right)).count() != 2:
                blocked(veto, "anti-link endpoints are missing or equal")
            pair, _ = rows(apps, db, "parties.PartySameAsPair").get_or_create(left_id=left, right_id=right)
            source = person_source(apps, db, veto)
            write(apps, db, cs, "PartySameAsPair", pair.pk, "same_as", source, "person", {}, preferred=True, snak="no_value")
            snapshot = {field.attname: getattr(pair, field.attname) for field in pair._meta.concrete_fields}
            rows(apps, db, "parties.HistoricalPartySameAsPair").create(
                **snapshot, changeset_id=cs.pk, history_type="+", history_date=timezone.now(), history_change_reason=REASON,
            )
            cs.touched.append("parties.partysameaspair")
            cs.counts["parties.partysameaspair"] = cs.counts.get("parties.partysameaspair", 0) + 1
        rows(apps, db, settings.ANGEE_CHANGESET_MODEL).filter(pk=cs.pk).update(
            counts=cs.counts, touched=sorted(set(cs.touched)),
        )


class Migration(migrations.Migration):
    dependencies = [("integrate", "__latest__"), migrations.swappable_dependency(settings.AUTH_USER_MODEL),
                    migrations.swappable_dependency(settings.ANGEE_CHANGESET_MODEL)]
    operations = [migrations.RunPython(forwards, migrations.RunPython.noop)]
