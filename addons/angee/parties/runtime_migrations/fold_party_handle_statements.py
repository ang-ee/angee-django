"""Fold proven carrier assertions, retaining the carrier until its reader cutover.

This is system work, never a REBAC policy evaluation. Historical base managers
write the statement and its native history under one migration Changeset; set()
admission is for new assertions only. Every referenced origin must still exist.
Missing principals, missing origins, conflicting producer clues, contradictory
decisions and decisions without a proven person actor block with PartyHandle's id.

Audit ownership alone does not identify a rule/importer or a decision actor.
Unattributed confirmed imports remain normal assertions (the #356 exception);
their count is printed. A legacy import label needs its owning addon's persisted
Integration attribution, supplied by a RecordLink targeting this carrier, or a canonical
Integration SubjectRef in that label. Never choose an account by its audit owner,
display name or backend, and never create a legacy.<source> principal.

Only pointers represented by the statement evidence contract are copied there.
The untouched carrier retains the original metadata, audit dates and flags for
the later retirement's parity review. Fold-time history dates record the fold,
not invented historical assertion times. No operational Handle/Party values are
re-derived here; parties' survivorship cutover is the next commit.

Carrier owner/editor/reader grants become Party reader grants, deliberately
granting subject-level (including private-field) reads. No edit grant is added.
Caveats and expiry are retained; incompatible caveat contexts block. Legacy
field-backed ownership is carried explicitly. Provision still migrates before
permission sync. The applied migration record freezes carrier writes.

Reverse is a no-op: it leaves both the untouched carriers and folded statements.
Removing the migration record reopens legacy writes; reapplying to occupied
statement keys refuses rather than overwriting newer evidence. Reversing the
preceding schema migrations drops the newly created statement/history tables.
"""

from django.apps import apps as live_apps
from django.conf import settings
from django.core.exceptions import ImproperlyConfigured, ValidationError
from django.db import migrations
from django.utils import timezone
from rebac import SubjectRef, app_settings, system_context
from rebac.resources import resource_id_attr, subject_id_attr

from angee.base.identity import EvidenceReference, instance_from_public_id, public_id_for
from angee.base.mixins import StatementSourceMixin

REASON = "fold.party_handle"


def applies(state):
    return all(key in state.models for key in (
        ("parties", "partyhandle"), ("parties", "partystatement"),
        ("parties", "historicalpartystatement"), ("provenance", "rule"), ("provenance", "seed"),
    ))


def _blocked(row, reason):
    raise ValueError(f"PartyHandle {row.pk} (party={row.party_id}, handle={row.handle_id}): {reason}")


def _rows(apps, label, db):
    # Historical state has physical fields only; native sqid ordering is virtual.
    return apps.get_model(label)._base_manager.using(db).order_by()


def _record(apps, db, row, ref):
    """Delegate public-id decoding to its identity owner, reading historical data."""
    try:
        ref = EvidenceReference.model_validate(ref)
        identity = live_apps.get_model(ref.model)
        origin = instance_from_public_id(identity, ref.id, queryset=_rows(apps, ref.model, db))
    except (LookupError, ValueError, TypeError, AttributeError) as error:
        _blocked(row, f"origin {ref!r} does not identify an existing row: {error}")
    if origin is None:
        _blocked(row, f"origin {ref.model}:{ref.id} is missing")
    return origin, ref.model_dump(mode="json")


def _principal(apps, db, row, label, pk, subject_type, kind):
    principal = _rows(apps, label, db).filter(pk=pk).first()
    if principal is None:
        _blocked(row, f"source {label}:{pk} is missing")
    if kind == "person" and principal.kind != "person":
        _blocked(row, f"source {label}:{pk} is not a person")
    identity = live_apps.get_model(label)
    ref = SubjectRef.of(subject_type, str(getattr(principal, subject_id_attr(identity))))
    return str(ref), kind


def _source(apps, db, row, party, refs, origins):
    metadata = row.metadata
    producer_evidence = metadata.get("evidence", {})
    if not isinstance(producer_evidence, dict):
        _blocked(row, "producer evidence must be a mapping")
    clues = [bool(producer_evidence), bool(metadata.get("claim")), bool(metadata.get("provenance"))]
    if sum(clues) > 1:
        _blocked(row, "merged metadata names several producers")
    if row.source == "manual":
        if any(clues):
            _blocked(row, "manual source conflicts with retained producer evidence")
        return (*_principal(apps, db, row, settings.AUTH_USER_MODEL, row.created_by_id,
                            app_settings.REBAC_USER_TYPE, "person"), None)
    if row.source == "carddav":
        if any(clues) or not party.folder_id or not party.source_uid:
            _blocked(row, "CardDAV source has no unambiguous retained card identity")
        folder = _rows(apps, "parties.Folder", db).filter(pk=party.folder_id).first()
        if folder is None:
            _blocked(row, f"origin Folder:{party.folder_id} is missing")
        directory = _rows(apps, "parties.Directory", db).filter(pk=folder.directory_id).first()
        if directory is None:
            _blocked(row, f"source Directory:{folder.directory_id} is missing")
        ref = {"model": "parties.Party", "id": public_id_for(live_apps.get_model("parties", "Party"), party.pk)}
        if ref not in refs:
            refs.append(ref)
        return (*_principal(apps, db, row, "integrate.Integration", directory.pk,
                            "integrate/integration", "directory"), None)
    if row.source in {"rule", "email_match"}:
        if metadata.get("claim") == "source_sender" and row.source == "email_match" and refs:
            key = "parties.source_sender"
        elif producer_evidence.get("kind") == "display_name" and row.source == "rule":
            origin, ref = _record(apps, db, row, {
                "model": "parties.Handle", "id": producer_evidence.get("source_handle", ""),
            })
            refs.append(ref)
            key = "parties.display_name"
        else:
            _blocked(row, "rule/matcher identity or typed origin was not retained")
        principal = _rows(apps, "provenance.Rule", db).filter(key=key).first()
        if principal is None:
            _blocked(row, f"source Rule:{key} is missing; bootstrap its persisted principal before folding")
        return (*_principal(apps, db, row, "provenance.Rule", principal.pk, "provenance/rule", "rule"), None)
    if row.source == "import":
        if producer_evidence or metadata.get("claim"):
            _blocked(row, "import source conflicts with retained rule/matcher evidence")
        content_types = _rows(apps, "contenttypes.ContentType", db)
        sources = {(origin.asserted_by, origin.pk) for origin in origins
                   if origin._meta.label_lower == "integrate.recordlink"
                   and origin.target_object_id == str(row.pk)
                   and content_types.filter(pk=origin.target_content_type_id, app_label="parties", model="partyhandle").exists()}
        label = metadata.get("provenance", "")
        if not isinstance(label, str):
            _blocked(row, "legacy import provenance must be a string")
        if label:
            try:
                ref = SubjectRef.parse(label)
            except (ValueError, TypeError):
                if not sources:
                    _blocked(row, f"legacy import label {label!r} has no persisted Integration attribution from its owner")
            else:
                if sources and {source for source, _ in sources} != {str(ref)}:
                    _blocked(row, "import label and producer RecordLink contradict one another")
                if not sources:
                    sources.add((str(ref), None))
        principals = {source for source, _ in sources}
        if len(principals) != 1:
            _blocked(row, f"import attribution is ambiguous or absent: {label!r}")
        try:
            ref = SubjectRef.parse(next(iter(principals)))
        except (ValueError, TypeError):
            _blocked(row, "producer RecordLink has no valid retained source identity")
        if ref.subject_type != "integrate/integration" or ref.optional_relation or ref.subject_id == "*":
            _blocked(row, f"import source {ref} is not one Integration")
        identity = live_apps.get_model("integrate", "Integration")
        try:
            principal = _rows(apps, "integrate.Integration", db).filter(
                **{subject_id_attr(identity): ref.subject_id},
            ).first()
        except (ValueError, TypeError):
            _blocked(row, f"source Integration:{ref.subject_id} has an invalid persisted identity")
        if principal is None:
            _blocked(row, f"source Integration:{ref.subject_id} is missing")
        if not refs:
            _blocked(row, "import origin was not retained")
        try:
            kind = StatementSourceMixin.kind_for(ref)
        except (ImproperlyConfigured, ValidationError) as error:
            _blocked(row, f"Integration source classification is not declared by its owner: {error}")
        if kind == "person":
            _blocked(row, "an Integration cannot be a person source")
        return str(ref), kind, next((pk for source, pk in sorted(sources, key=str) if pk is not None), None)
    _blocked(row, f"source {row.source!r} has no proven persisted principal")


def _decision(apps, db, row):
    """A flag's transition history identifies its actor; audit ownership does not."""
    if row.is_confirmed and row.is_dismissed:
        _blocked(row, "confirmed and dismissed flags contradict one another")
    if not (row.is_confirmed or row.is_dismissed):
        return None
    flag = "is_confirmed" if row.is_confirmed else "is_dismissed"
    try:
        history = apps.get_model("parties", "HistoricalPartyHandle")
    except LookupError:
        history = None
    actor = None
    if history is not None:
        previous = False
        for version in _rows(apps, history._meta.label, db).filter(id=row.pk).order_by("history_date", "history_id"):
            if getattr(version, flag) and not previous:
                actor = version.history_user_id
            previous = getattr(version, flag)
        if not previous:
            actor = None
    if actor is None:
        if row.source == "import" and row.is_confirmed:
            return "unattributed_import"
        _blocked(row, "decision has no proven person actor in its transition history")
    return _principal(apps, db, row, settings.AUTH_USER_MODEL, actor, app_settings.REBAC_USER_TYPE, "person")[0]


def _carry_readers(apps, db, row, party):
    """Copy grant data, never inspect schema permissions or evaluate a policy."""
    model = apps.get_model(app_settings.REBAC_RELATIONSHIP_MODEL)
    rows = _rows(apps, app_settings.REBAC_RELATIONSHIP_MODEL, db)
    carrier_id = str(getattr(row, resource_id_attr(live_apps.get_model("parties", "PartyHandle"))))
    party_id = str(getattr(party, resource_id_attr(live_apps.get_model("parties", "Party"))))
    grants = list(rows.filter(resource_type="parties/party_handle", resource_id=carrier_id,
                             relation__in=("owner", "editor", "reader")).order_by("pk"))
    if row.created_by_id:
        source, _ = _principal(apps, db, row, settings.AUTH_USER_MODEL, row.created_by_id,
                               app_settings.REBAC_USER_TYPE, None)
        ref = SubjectRef.parse(source)
        grants.append(model(subject_type=ref.subject_type, subject_id=ref.subject_id))
    for grant in grants:
        key = dict(resource_type="parties/party", resource_id=party_id, relation="reader",
                   subject_type=grant.subject_type, subject_id=grant.subject_id,
                   optional_subject_relation=grant.optional_subject_relation, caveat_name=grant.caveat_name)
        existing, created = rows.get_or_create(**key, defaults={
            "caveat_context": grant.caveat_context, "expires_at": grant.expires_at,
        })
        if not created:
            if existing.caveat_context != grant.caveat_context:
                _blocked(row, "Party reader grant has incompatible caveat context")
            if existing.expires_at is not None:
                expires = None if grant.expires_at is None else max(existing.expires_at, grant.expires_at)
                rows.filter(pk=existing.pk).update(expires_at=expires)


def forwards(apps, schema_editor):
    db = schema_editor.connection.alias
    carriers = _rows(apps, "parties.PartyHandle", db)
    if not carriers.exists():
        return
    statements = _rows(apps, "parties.PartyStatement", db)
    history = _rows(apps, "parties.HistoricalPartyStatement", db)
    cs_model = apps.get_model(settings.ANGEE_CHANGESET_MODEL)
    with system_context(reason=REASON):
        cs = cs_model._base_manager.using(db).create(activity="system", change_reason=REASON,
                                                   state="closed", outcome="success")
        count = unattributed = 0
        for row in carriers.order_by("pk").iterator():
            party = _rows(apps, "parties.Party", db).filter(pk=row.party_id).first()
            handle = _rows(apps, "parties.Handle", db).filter(pk=row.handle_id).first()
            if party is None or handle is None:
                _blocked(row, "subject or member origin is missing")
            if not isinstance(row.metadata, dict) or not 0 <= row.confidence <= 1:
                _blocked(row, "invalid retained metadata or confidence")
            refs, origins = [], []
            evidence = row.metadata.get("evidence_refs", [])
            if not isinstance(evidence, list):
                _blocked(row, "origin references must be a list")
            for ref in evidence:
                origin, ref = _record(apps, db, row, ref)
                origins.append(origin)
                if ref not in refs:
                    refs.append(ref)
            source, kind, link_id = _source(apps, db, row, party, refs, origins)
            decision = _decision(apps, db, row)
            if decision == "unattributed_import":
                unattributed += 1
                decision = None
            assertions = [(source, kind, "normal", "value", row.confidence)]
            if decision is not None:
                assertions.append((decision, "person", "preferred", "value" if row.is_confirmed else "no_value", 1.0))
            for asserted_by, source_kind, rank, snak, confidence in assertions:
                key = dict(subject_id=row.party_id, handle_id=row.handle_id, aspect="handle", asserted_by=asserted_by)
                existing = statements.filter(**key, superseded_id=None).first()
                if existing is not None and existing.changeset_id != cs.pk:
                    _blocked(row, f"statement key {asserted_by} already contains newer evidence")
                values = dict(source_kind=source_kind, rank=rank, snak=snak, confidence=confidence,
                              evidence=refs, link_id=link_id, changeset_id=cs.pk, status="asserted", applied=False)
                statement, created = statements.update_or_create(**key, superseded_id=None, defaults=values)
                snapshot = {field.attname: getattr(statement, field.attname)
                            for field in statement._meta.concrete_fields}
                history.create(**snapshot, history_date=timezone.now(), history_type="+" if created else "~",
                               history_change_reason=REASON)
                count += 1
            _carry_readers(apps, db, row, party)
        cs_model._base_manager.using(db).filter(pk=cs.pk).update(
            counts={"parties.partystatement": count}, touched=["parties.partystatement"],
        )
        print(f"PartyHandle fold: {carriers.count()} carriers, {count} assertions/decisions; "
              f"{unattributed} confirmed imports without a person actor retained at normal rank.")


class Migration(migrations.Migration):
    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        migrations.swappable_dependency(settings.ANGEE_CHANGESET_MODEL),
        ("provenance", "__latest__"), ("rebac", "__latest__"),
    ]
    operations = [migrations.RunPython(forwards, migrations.RunPython.noop)]
