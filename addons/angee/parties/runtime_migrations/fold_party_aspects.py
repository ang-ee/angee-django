"""Fold identity, address, relationship and anti-link facts into aspect statements.

System work under one migration Changeset through historical base managers; no
REBAC policy is evaluated and no actor is ever invented. Attribution per row:

- A directory-enrolled party (folder and stable key; a ``carddav`` relationship)
  needs its folder, Directory and the RecordLink targeting the card; a missing one
  blocks with the row named. Values equal to the link's applied snapshot (mapped
  values compared normalized, never raw vCard bytes or etags) are a normal
  DIRECTORY statement by the Directory with the link as evidence. A value that
  differs is a local edit: a normal PERSON statement by the recorded editor
  (``updated_by``, else ``created_by``) when that account is a person, otherwise
  the value stays on the canonical column unasserted and is counted.
- Any other party: a normal PERSON statement by the recorded editor when a person.
  A party whose legacy carriers are ``import`` links (connections takeout) and an
  ``import`` relationship are left unasserted and counted: IMPORT attribution awaits
  its ruling. Relationship sources outside manual/carddav/import block.
- A MergeVeto is a person's anti-link: a preferred ``no_value`` ``same_as`` statement
  by its recorded person on the ordered pair. A veto without a person blocks.
- Legacy extension data is retained on the card's link payload (the evidence owner);
  a payload that already holds different extension data blocks (a re-run state).
  Folded handle claims of a directory party gain their card link.

Every blocking row is collected before anything is written; ``plan()`` is the
predicate the parties fold preflight evaluates on the pre-upgrade state. Statement
rows carry the row's ``created_at``/``updated_at``; history rows record the fold
time. Existing occupied statement keys block rather than overwrite evidence.

Reverse is a no-op: provenance stays retained alongside the legacy columns until
the separate retirement.
"""

import base64
import datetime
from collections import Counter
from decimal import Decimal

from django.conf import settings
from django.db import migrations
from django.utils import timezone
from rebac import SubjectRef, app_settings, system_context
from sqids import Sqids
from sqids.constants import DEFAULT_ALPHABET, DEFAULT_MIN_LENGTH

REASON = "fold.party_aspects"
ASPECTS = {
    "Party": {"identity": ("display_name",), "contact": ("notes", "avatar")},
    "Person": {"name": ("name_prefix", "given_name", "additional_name", "family_name", "name_suffix", "nickname"),
               "dates": ("birthday", "anniversary")},
    "Organization": {"legal_identity": ("legal_name", "domain")},
    "Address": {"address": ("label", "po_box", "extended", "street", "city", "region", "postal_code", "country")},
    "Relationship": {"relationship": ("other_name", "title", "started_at", "ended_at", "notes")},
}
PREFIXES = {"parties.party": "pty_", "parties.person": "pty_", "parties.organization": "pty_", "parties.address": "adr_",
            "parties.relationship": "rel_", "parties.partysameaspair": "", "integrate.recordlink": "rlk_"}


def applies(state):
    party = state.models.get(("parties", "party"))
    return party is not None and "source_uid" in party.fields and ("parties", "partysameaspairstatement") in state.models


class Blocked(ValueError):
    """One row the fold refuses, named for the operator."""

    def __init__(self, label, pk, reason):
        super().__init__(f"{label} {pk}: {reason}")
        self.label, self.pk, self.reason = label, pk, reason


def json_value(value):
    """JSON-safe spelling of one column value, as the mapped payload stores it."""
    if value is None or isinstance(value, bool | int | str):
        return value
    if isinstance(value, float):
        return value
    if isinstance(value, datetime.datetime | datetime.date | datetime.time):
        return value.isoformat()
    if isinstance(value, Decimal):
        return str(value)
    if isinstance(value, bytes):
        return base64.b64encode(value).decode("ascii")
    if isinstance(value, list | tuple):
        return [json_value(item) for item in value]
    if isinstance(value, dict):
        return {str(key): json_value(item) for key, item in value.items()}
    return str(value)


class Legacy:
    """Historical-model reads shared by the fold and the preflight."""

    def __init__(self, apps, db):
        self.apps, self.db = apps, db
        self.codec = Sqids(min_length=getattr(settings, "DJANGO_SQIDS_MIN_LENGTH", None) or DEFAULT_MIN_LENGTH,
                           alphabet=getattr(settings, "DJANGO_SQIDS_ALPHABET", None) or DEFAULT_ALPHABET)
        self.user_kinds = dict(self.rows(settings.AUTH_USER_MODEL).values_list("pk", "kind"))
        self.imported = set()
        if self.model("parties.PartyHandle") is not None:
            self.imported = set(self.rows("parties.PartyHandle").filter(source="import").values_list("party_id", flat=True))
        self.parties = {}

    def model(self, label):
        try:
            return self.apps.get_model(label)
        except LookupError:
            return None

    def rows(self, label):
        # Historical state has physical fields only; native sqid ordering is virtual.
        return self.apps.get_model(label)._base_manager.using(self.db).order_by()

    def party(self, pk):
        if pk not in self.parties:
            self.parties[pk] = self.rows("parties.Party").filter(pk=pk).first()
        return self.parties[pk]

    def public_id(self, label, pk):
        return f"{PREFIXES[label.lower()]}{self.codec.encode([int(pk)])}"

    def person(self, row):
        actor = next((pk for pk in (row.updated_by_id, row.created_by_id)
                      if pk is not None and self.user_kinds.get(pk) == "person"), None)
        return None if actor is None else ("user", actor)

    def directory_link(self, row, party):
        """Return (integration_id, link) for an enrolled card; structural gaps block."""
        folder = self.rows("parties.Folder").filter(pk=party.folder_id).first()
        if folder is None or not folder.directory_id or not party.source_uid:
            raise Blocked(row._meta.label, row.pk, "directory fact has no stable folder/key attribution")
        if not self.rows("integrate.Integration").filter(pk=folder.directory_id).exists():
            raise Blocked(row._meta.label, row.pk, "directory principal is missing")
        if not self.rows("parties.Directory").filter(pk=folder.directory_id).exists():
            raise Blocked(row._meta.label, row.pk, "directory classification has no historical Directory child row")
        link = self.rows("integrate.RecordLink").filter(
            stream__integration_id=folder.directory_id, stream__key="contacts", stream__partition=folder.source_href,
            external_key=party.source_uid, target_object_id=str(party.pk),
            target_content_type__app_label="parties", target_content_type__model__in=("party", "person", "organization"),
        ).order_by("-stream__generation", "pk").first()
        if link is None:
            raise Blocked(row._meta.label, row.pk, "no observed RecordLink targets the retained directory identity")
        return folder.directory_id, link

    def applied_payload(self, link):
        """The mapped values the directory last applied, or None when nothing was applied.

        After integrate's fold the owner is the link's pinned history row; on the
        pre-upgrade state the preflight evaluates, it is the latest applied legacy
        ``RecordRevision``, which that fold converts into exactly that row.
        """
        base_id = getattr(link, "applied_base_id", None)
        if base_id is not None:
            base = self.rows("integrate.HistoricalRecordLink").filter(history_id=base_id).first()
            return None if base is None else (base.mapped_payload or {})
        if self.model("integrate.RecordRevision") is None:
            return None
        revision = self.rows("integrate.RecordRevision").filter(
            link_id=link.pk, applied_at__isnull=False,
        ).order_by("-number").first()
        return None if revision is None else (revision.mapped_payload or {})

    def proven(self, row, name, fields, payload):
        """Whether the row's aspect values equal the applied snapshot's, blanks normalized."""
        values = {field: json_value(getattr(row, row._meta.get_field(field).attname)) for field in fields}
        if name == "Address":
            return any(isinstance(entry, dict) and all(same(entry.get(key), value) for key, value in values.items())
                       for entry in payload.get("addresses", []))
        if name == "Relationship":
            expected = {"other_name": payload.get("organization"), "title": payload.get("title"),
                        "notes": payload.get("role"), "started_at": None, "ended_at": None}
            return all(same(expected[key], value) for key, value in values.items())
        for field, value in values.items():
            expected = payload.get(field)
            if field == "display_name":
                expected = expected or payload.get("family_name") or "Unknown"
            elif field == "avatar":
                expected = None
                if value is not None:
                    file = self.rows("storage.File").filter(pk=value).first()
                    if file is not None and (payload.get("photo") or {}).get("hash") == file.content_hash:
                        expected = value
            if not same(expected, value):
                return False
        return True


def same(expected, value):
    """Mapped values compare normalized: an absent or empty value is blank either way."""
    return expected == value or (expected in (None, "") and value in (None, ""))


def classify(legacy, name, row, notices):
    """Return (party, integration_id, link, statements); each statement is (aspect, principal, kind, link, values)."""
    party = row if name == "Party" else legacy.party(row.party_id if name in ("Address", "Relationship") else row.pk)
    if party is None:
        raise Blocked(row._meta.label, row.pk, "owning party is missing")
    directory = row.source == "carddav" if name == "Relationship" else bool(party.folder_id and party.source_uid)
    if name == "Relationship" and row.source not in ("manual", "carddav", "import"):
        raise Blocked(row._meta.label, row.pk, f"legacy relationship source {row.source!r} has no proven principal")
    imported = (name == "Relationship" and row.source == "import") or (not directory and party.pk in legacy.imported)
    integration_id = link = payload = None
    if directory:
        integration_id, link = legacy.directory_link(row, party)
        payload = legacy.applied_payload(link)
    statements = []
    for aspect, fields in ASPECTS[name].items():
        values = {row._meta.get_field(field).attname: getattr(row, row._meta.get_field(field).attname) for field in fields}
        if directory and payload is not None and legacy.proven(row, name, fields, payload):
            statements.append((aspect, ("integration", integration_id), "directory", link, values))
            continue
        if imported:
            notices[f"{name}.{aspect}: imported value with no ruled IMPORT principal, left unasserted"] += 1
            continue
        person = legacy.person(row)
        if person is None:
            reason = "local edit of a directory value" if directory else "value"
            notices[f"{name}.{aspect}: {reason} with no recorded person actor, left unasserted"] += 1
            continue
        if directory:
            notices[f"{name}.{aspect}: local edit of a directory value, attributed to the editing person"] += 1
        statements.append((aspect, person, "person", None, values))
    return party, integration_id, link, statements


def plan(apps, db):
    """Classify every row and veto: (legacy, plans, vetoes, blockers, notices)."""
    legacy = Legacy(apps, db)
    plans, vetoes, blockers, notices = [], [], [], Counter()
    for name in ASPECTS:
        for row in legacy.rows("parties." + name).order_by("pk").iterator():
            try:
                plans.append((name, row, classify(legacy, name, row, notices)))
            except Blocked as blocked:
                blockers.append(blocked)
    veto_model = legacy.model("parties.MergeVeto")
    if veto_model is not None:
        for veto in legacy.rows("parties.MergeVeto").order_by("pk").iterator():
            left, right = sorted((veto.party_a_id, veto.party_b_id))
            if left == right or legacy.rows("parties.Party").filter(pk__in=(left, right)).count() != 2:
                blockers.append(Blocked("parties.MergeVeto", veto.pk, "anti-link endpoints are missing or equal"))
                continue
            person = legacy.person(veto)
            if person is None:
                blockers.append(Blocked("parties.MergeVeto", veto.pk, "anti-link decision has no recorded person actor"))
                continue
            vetoes.append((veto, left, right, person))
    return legacy, plans, vetoes, blockers, notices


def _principal(principal):
    kind, value = principal
    if kind == "user":
        return str(SubjectRef.of(app_settings.REBAC_USER_TYPE, str(value)))
    return str(SubjectRef.of("integrate/integration", str(value)))


class Writer:
    """Batched statement and history writes under the fold changeset."""

    def __init__(self, legacy, cs):
        self.legacy, self.cs, self.now = legacy, cs, timezone.now()
        self.batch = []
        self.counts = Counter()

    def statement(self, name, subject, aspect, principal, kind, values, *, link=None, preferred=False, snak="value"):
        statements = self.legacy.rows("parties." + name + "Statement")
        asserted_by = _principal(principal)
        key = dict(subject_id=subject.pk, aspect=aspect, asserted_by=asserted_by, superseded_id=None)
        if statements.filter(**key).exists():
            raise Blocked(f"parties.{name}", subject.pk, f"occupied {aspect} statement key {asserted_by}")
        withdrawn = link is not None and link.status in ("withdrawn", "tombstone")
        evidence_label, evidence_pk = ("integrate.RecordLink", link.pk) if link else ("parties." + name, subject.pk)
        row = statements.model(
            **key, **values, source_kind=kind, rank="preferred" if preferred else "normal", snak=snak,
            status="withdrawn" if withdrawn else "asserted", applied=not withdrawn, confidence=1.0,
            link_id=link.pk if link else None, changeset_id=self.cs.pk,
            evidence=[{"model": evidence_label, "id": self.legacy.public_id(evidence_label, evidence_pk)}],
            created_at=subject.created_at, updated_at=subject.updated_at,
        )
        self.add(row, "+")

    def add(self, row, kind):
        self.batch.append((row, kind))
        self.counts[row._meta.label_lower] += 1
        if len(self.batch) >= 500:
            self.flush()

    def flush(self):
        by_model = {}
        for row, kind in self.batch:
            by_model.setdefault(type(row), []).append((row, kind))
        for model, records in by_model.items():
            created = [row for row, _ in records if row._state.adding]
            stamps = [(row, row.created_at, row.updated_at) for row in created]
            model._base_manager.using(self.legacy.db).bulk_create(created, batch_size=500)
            for row, created_at, updated_at in stamps:
                row.created_at, row.updated_at = created_at, updated_at
            if created and all(hasattr(row, "created_at") for row in created):
                model._base_manager.using(self.legacy.db).bulk_update(created, ("created_at", "updated_at"), batch_size=500)
            history = self.legacy.model(model._meta.app_label + ".Historical" + model._meta.object_name)
            if history is None:
                continue
            history._base_manager.using(self.legacy.db).bulk_create([
                history(**({field.attname: getattr(row, field.attname) for field in row._meta.concrete_fields}
                           | {"changeset_id": self.cs.pk}),
                        history_type=kind, history_date=self.now, history_change_reason=REASON)
                for row, kind in records
            ], batch_size=500)
        self.batch.clear()


def forwards(apps, schema_editor):
    db = schema_editor.connection.alias
    legacy, plans, vetoes, blockers, notices = plan(apps, db)
    if blockers:
        raise ValueError(f"Party aspect fold: {len(blockers)} rows block this database:\n" + "\n".join(map(str, blockers)))
    with system_context(reason=REASON):
        cs = legacy.rows(settings.ANGEE_CHANGESET_MODEL).create(
            activity="system", change_reason=REASON, state="closed", outcome="success",
        )
        writer = Writer(legacy, cs)
        links = legacy.rows("integrate.RecordLink")
        for name, row, (party, integration_id, link, statements) in plans:
            for aspect, principal, kind, statement_link, values in statements:
                writer.statement(name, row, aspect, principal, kind, values, link=statement_link)
            if name != "Party" or link is None:
                continue
            if party.extensions:
                # Payload is the evidence owner; retain old extension data there.
                payload = dict(link.source_payload)
                if "extensions" in payload and payload["extensions"] != party.extensions:
                    raise Blocked(row._meta.label, row.pk, "extension payload contradicts retained evidence")
                payload["extensions"] = party.extensions
                if link.source_payload != payload:
                    link.source_payload = payload
                    links.filter(pk=link.pk).update(source_payload=payload)
                    writer.add(link, "~")
            claims = legacy.rows("parties.PartyStatement").filter(
                subject_id=row.pk, aspect="handle", source_kind="directory",
                asserted_by=_principal(("integration", integration_id)), link_id=None,
            )
            for claim in claims.order_by("pk"):
                claim.link_id, claim.changeset_id = link.pk, cs.pk
                claims.filter(pk=claim.pk).update(link_id=link.pk, changeset_id=cs.pk)
                writer.add(claim, "~")
        pairs = legacy.rows("parties.PartySameAsPair")
        for veto, left, right, person in vetoes:
            pair, created = pairs.get_or_create(left_id=left, right_id=right)
            if created:
                writer.add(pair, "+")
            writer.statement("PartySameAsPair", pair, "same_as", person, "person", {}, preferred=True, snak="no_value")
        writer.flush()
        legacy.rows(settings.ANGEE_CHANGESET_MODEL).filter(pk=cs.pk).update(
            counts=dict(writer.counts), touched=sorted(writer.counts),
        )
        print(f"Party aspect fold: {sum(writer.counts.values())} rows written under changeset {cs.pk}.")
        for reason, number in sorted(notices.items()):
            print(f"Party aspect fold: {number} x {reason}")


class Migration(migrations.Migration):
    dependencies = [("integrate", "__latest__"), migrations.swappable_dependency(settings.AUTH_USER_MODEL),
                    migrations.swappable_dependency(settings.ANGEE_CHANGESET_MODEL)]
    operations = [migrations.RunPython(forwards, migrations.RunPython.noop)]
