"""Fold legacy PartyHandle carriers into PartyStatement rows, retaining the carrier.

System work, never a REBAC policy evaluation: historical base managers write the
statement and its native history under one migration Changeset. The body reads
historical models only; principal identities are the authoring-time facts of this
schema (every principal's subject id is its primary key; public ids are sqids under
the model prefixes below).

Retained evidence, per carrier source:

- ``carddav``: a normal DIRECTORY statement asserted by the card's Directory
  integration, with the Party as evidence.
- ``manual`` by a person: a normal PERSON statement asserted by the creator.
- ``email_match`` / ``rule``: a normal RULE statement asserted by the seeded Rule
  principal that names the producer — ``parties.normalized_twin`` (the email twin
  matcher), ``parties.email_domain`` (the organization domain rule),
  ``parties.display_name``, ``parties.signature_phone``, ``parties.source_sender``.
  The Rule row is created here idempotently under the install seed's key; the fold
  never waits for the resource loader. Rule rows with an unknown evidence kind block.
  A claimed sender written before 2026-10-10 keeps its references under ``evidence``
  as a list; they are read as the claim's references (``_retained_metadata``).
- ``import`` with a RecordLink origin or a canonical Integration label: a normal
  DIRECTORY statement by that Integration. A takeout import (``social_takeout`` or
  any label that is not a SubjectRef, with no RecordLink) is one IMPORT source per
  imported takeout account (ruling, 2026-10-12): the importer's channel for the
  handle's platform (``messaging.Channel`` owned by the carrier's creator with that
  ``backend_class``) asserts a normal IMPORT statement. Without exactly one such
  channel the link is left unasserted and counted: the
  handle stays linked only through other proof, the count is printed, and IMPORT
  attribution awaits its ruling. Contradictory import attribution blocks.
- ``oauth``: the login self-claim is the owning user's own decision, a preferred
  PERSON statement by the handle's owner (else its creator).
- A decided ``manual`` carrier that still names a producer (a claim, rule evidence, an
  import label): until 2026-10-11 a confirmation rewrote the producer's source to
  ``manual`` and kept its metadata, and a later dismissal kept both, so it folds as every
  older decided link does, the person's (a source assertion by its creator and the
  decider's stance), with the producer's record references as evidence; producer clues
  on an undecided ``manual`` carrier or an ``oauth`` one still block.
- Confirm/dismiss: a preferred PERSON stance by the recorded decider (``updated_by``,
  else ``created_by``) when that account is a person. A decision whose recorded
  actor is a service account or absent is a system decision: a normal statement
  asserted by this fold's Seed principal (``parties.fold_party_handle``), keeping
  the recorded account as evidence. Nothing is ever attributed to an invented actor.

Statement rows carry the carrier's ``created_at``/``updated_at``; history rows
record the fold time. Only pointers that fit the statement evidence contract are
copied; the untouched carrier retains the original metadata and flags for the
retirement's parity review. Carrier owner/editor/reader grants become Party
``carrier_reader`` grants (read only). The applied statement per (party, handle)
is the decisive one: a preferred stance, else the strongest normal assertion at the
review threshold, unless another fold (a consumer's own, in the same round) already
applied the pair: then the fold's statements are recorded beside it, counted, and the
migrate that ran the folds resolves the pair once it completes
(``PartyManager.resolve_folded_statements``), so the ranking decides the published value.

Every blocking row is collected before anything is written; the first error names
them all. ``plan()`` is the predicate the parties fold preflight evaluates against
the pre-upgrade state, so blockers surface before any destructive declaration.

Reverse is a no-op: it leaves both the untouched carriers and the folded statements.
Removing the migration record reopens legacy writes; reapplying to occupied
statement keys refuses rather than overwriting newer evidence. Reversing the
preceding schema migrations drops the statement/history tables.
"""

from collections import Counter
from itertools import batched

from django.conf import settings
from django.db import migrations
from django.utils import timezone
from rebac import SubjectRef, app_settings, system_context
from sqids import Sqids
from sqids.constants import DEFAULT_ALPHABET, DEFAULT_MIN_LENGTH

REASON = "fold.party_handle"
SYSTEM_KEY = "parties.fold_party_handle"
PREFIXES = {"parties.party": "pty_", "parties.person": "pty_", "parties.organization": "pty_",
            "parties.handle": "hdl_", "integrate.recordlink": "rlk_", settings.AUTH_USER_MODEL.lower(): "usr_"}
RULE_KEYS = {("email_match", None): "parties.normalized_twin", ("email_match", "source_sender"): "parties.source_sender",
             ("rule", None): "parties.email_domain", ("rule", "display_name"): "parties.display_name",
             ("rule", "signature_phone"): "parties.signature_phone", ("rule", "source_sender"): "parties.source_sender"}
REVIEW_CONFIDENCE = 0.5


def applies(state):
    return all(key in state.models for key in (
        ("parties", "partyhandle"), ("parties", "partystatement"),
        ("parties", "historicalpartystatement"), ("provenance", "rule"), ("provenance", "seed"),
    ))


class Blocked(ValueError):
    """One row the fold refuses, named for the operator."""

    def __init__(self, label, pk, reason):
        super().__init__(f"{label} {pk}: {reason}")
        self.label, self.pk, self.reason = label, pk, reason


class Legacy:
    """Historical-model reads shared by the fold and the preflight."""

    def __init__(self, apps, db):
        self.apps, self.db = apps, db
        self.codec = Sqids(min_length=getattr(settings, "DJANGO_SQIDS_MIN_LENGTH", None) or DEFAULT_MIN_LENGTH,
                           alphabet=getattr(settings, "DJANGO_SQIDS_ALPHABET", None) or DEFAULT_ALPHABET)
        self.user_kinds = dict(self.rows(settings.AUTH_USER_MODEL).values_list("pk", "kind"))
        self.integrations = dict(self.rows("integrate.SyncStream").values_list("pk", "integration_id"))

    def model(self, label):
        try:
            return self.apps.get_model(label)
        except LookupError:
            return None

    def rows(self, label):
        # Historical state has physical fields only; native sqid ordering is virtual.
        return self.apps.get_model(label)._base_manager.using(self.db).order_by()

    def person(self, pk):
        return pk is not None and self.user_kinds.get(pk) == "person"

    def public_id(self, label, pk):
        return f"{PREFIXES.get(label.lower(), '')}{self.codec.encode([int(pk)])}"

    def decode(self, value):
        _, separator, encoded = str(value).rpartition("_")
        decoded = self.codec.decode(encoded if separator else str(value))
        return decoded[0] if len(decoded) == 1 else None

    def record(self, row, ref):
        """Resolve one retained evidence reference to its historical row."""
        if not isinstance(ref, dict) or not isinstance(ref.get("model"), str) or not isinstance(ref.get("id"), str):
            raise Blocked("PartyHandle", row.pk, f"origin {ref!r} is not a model/id reference")
        model = self.model(ref["model"])
        if model is None:
            raise Blocked("PartyHandle", row.pk, f"origin model {ref['model']!r} is not in this schema")
        pk = self.decode(ref["id"])
        origin = model._base_manager.using(self.db).order_by().filter(pk=pk).first() if pk is not None else None
        if origin is None:
            raise Blocked("PartyHandle", row.pk, f"origin {ref['model']}:{ref['id']} is missing")
        return origin, {"model": model._meta.label, "id": ref["id"]}

    def user_ref(self, pk):
        label = self.apps.get_model(settings.AUTH_USER_MODEL)._meta.label
        return {"model": label, "id": self.public_id(label, pk)}

    def takeout_account(self, owner_id, platform):
        """The one channel ``owner_id`` imports ``platform`` through, or None (messaging absent, none, or several)."""
        channel = self.model("messaging.Channel")
        if channel is None or owner_id is None or not platform:
            return None
        owned = self.rows("integrate.Integration").filter(owner_id=owner_id).values_list("pk", flat=True)
        # The backend key is compared in Python: the field validates lookup values against
        # the installed backends, and a platform without an installed bridge is not an error.
        keys = self.rows("messaging.Channel").filter(integration_ptr_id__in=owned).values_list("pk", "backend_class")
        matches = [pk for pk, key in keys if str(getattr(key, "value", key)) == platform]
        return matches[0] if len(matches) == 1 else None


def _retained_metadata(row, notices):
    """The carrier's metadata in the spelling the current producers write.

    Until 2026-10-10 a claimed sender kept its record references under ``evidence``
    as a list, the key a rule suggestion keeps its evidence mapping under, so a claim
    landing on a suggestion replaced that mapping with the list of its keys (and the
    suggestion kept its ``rule`` source). The list's references are the claim's
    ``evidence_refs``; the leftover key names refer to nothing and are dropped, counted.
    """
    metadata = row.metadata
    listed, refs = metadata.get("evidence"), metadata.get("evidence_refs", [])
    if not metadata.get("claim") or not isinstance(listed, list) or not isinstance(refs, list):
        return metadata
    refs = list(refs)
    if any(not isinstance(item, dict) for item in listed):
        notices["source_sender claim over a rule suggestion: its overwritten evidence keys dropped"] += 1
    refs.extend(item for item in listed if isinstance(item, dict) and item not in refs)
    return {**{key: value for key, value in metadata.items() if key != "evidence"}, "evidence_refs": refs}


def _source(legacy, row, metadata, party, handle, refs, origins, notices):
    """Return the carrier's source principal as (principal, kind, link_id), or None when unasserted."""
    producer_evidence = metadata.get("evidence", {})
    if not isinstance(producer_evidence, dict):
        raise Blocked("PartyHandle", row.pk, "producer evidence must be a mapping")
    clues = [bool(producer_evidence), bool(metadata.get("claim")), bool(metadata.get("provenance"))]
    if sum(clues) > 1:
        raise Blocked("PartyHandle", row.pk, "merged metadata names several producers")
    if row.source in ("manual", "oauth"):
        decided = row.is_confirmed or row.is_dismissed
        if any(clues) and not (row.source == "manual" and decided):
            raise Blocked("PartyHandle", row.pk, f"{row.source} source conflicts with retained producer evidence")
        if any(clues):
            # Until 2026-10-11 a confirmation rewrote the producer's source to "manual" and left
            # its metadata, and a later dismissal kept both: the person's link, decided as recorded,
            # with the producer's references as evidence.
            notices["producer link a person decided before its source was kept: folded as the person's"] += 1
        owner = handle.owner_id if row.source == "oauth" else None
        actor = next((pk for pk in (owner, row.created_by_id) if legacy.person(pk)), None)
        if actor is not None:
            return ("user", actor), "person", None
        recorded = owner or row.created_by_id
        if recorded is not None:
            refs.append(legacy.user_ref(recorded))
        notices[f"{row.source} assertion by a service account or without an actor: attributed to the fold"] += 1
        return ("seed", SYSTEM_KEY), "seed", None
    if row.source == "carddav":
        if any(clues) or not party.folder_id or not party.source_uid:
            raise Blocked("PartyHandle", row.pk, "CardDAV source has no unambiguous retained card identity")
        folder = legacy.rows("parties.Folder").filter(pk=party.folder_id).first()
        if folder is None:
            raise Blocked("PartyHandle", row.pk, f"origin Folder:{party.folder_id} is missing")
        if not legacy.rows("parties.Directory").filter(pk=folder.directory_id).exists():
            raise Blocked("PartyHandle", row.pk, f"source Directory:{folder.directory_id} is missing")
        ref = {"model": "parties.Party", "id": legacy.public_id("parties.party", party.pk)}
        if ref not in refs:
            refs.append(ref)
        return ("integration", folder.directory_id), "directory", None
    if row.source in ("rule", "email_match"):
        kind = producer_evidence.get("kind") if producer_evidence else metadata.get("claim")
        if kind == "source_sender" and not refs:
            raise Blocked("PartyHandle", row.pk, "source_sender claim retained no message evidence")
        if kind == "display_name":
            _, ref = legacy.record(row, {"model": "parties.Handle", "id": str(producer_evidence.get("source_handle", ""))})
            if ref not in refs:
                refs.append(ref)
        key = RULE_KEYS.get((row.source, kind))
        if key is None:
            raise Blocked("PartyHandle", row.pk, f"{row.source} producer {kind!r} has no seeded Rule key")
        return ("rule", key), "rule", None
    if row.source == "import":
        if producer_evidence or metadata.get("claim"):
            raise Blocked("PartyHandle", row.pk, "import source conflicts with retained rule/matcher evidence")
        carrier_types = set(legacy.rows("contenttypes.ContentType").filter(
            app_label="parties", model="partyhandle",
        ).values_list("pk", flat=True))
        sources = {(legacy.integrations.get(origin.stream_id), origin.pk) for origin in origins
                   if origin._meta.label_lower == "integrate.recordlink"
                   and origin.target_object_id == str(row.pk) and origin.target_content_type_id in carrier_types}
        label = metadata.get("provenance", "")
        if not isinstance(label, str):
            raise Blocked("PartyHandle", row.pk, "legacy import provenance must be a string")
        labelled = None
        if label:
            try:
                labelled = SubjectRef.parse(label)
            except (ValueError, TypeError):
                labelled = None
            else:
                if labelled.subject_type != "integrate/integration" or labelled.optional_relation or labelled.subject_id == "*":
                    raise Blocked("PartyHandle", row.pk, f"import source {labelled} is not one Integration")
                try:
                    integration_id = int(labelled.subject_id)
                except ValueError:
                    raise Blocked("PartyHandle", row.pk, f"source Integration:{labelled.subject_id} has an invalid identity") from None
                if sources and {pk for pk, _ in sources} != {integration_id}:
                    raise Blocked("PartyHandle", row.pk, "import label and producer RecordLink contradict one another")
                if not sources:
                    sources.add((integration_id, None))
        if not sources:
            account = legacy.takeout_account(row.created_by_id, handle.platform)
            if account is None:
                notices[f"takeout import without one identified account channel ({label or 'unlabelled'}): "
                        "left unasserted"] += 1
                return None
            return ("integration", account), "import", None
        integration_ids = {pk for pk, _ in sources}
        if len(integration_ids) != 1:
            raise Blocked("PartyHandle", row.pk, f"import attribution is ambiguous: {sorted(map(str, integration_ids))}")
        integration_id = next(iter(integration_ids))
        if not legacy.rows("integrate.Integration").filter(pk=integration_id).exists():
            raise Blocked("PartyHandle", row.pk, f"source Integration:{integration_id} is missing")
        if not legacy.rows("parties.Directory").filter(pk=integration_id).exists():
            raise Blocked("PartyHandle", row.pk, "Integration source classification has no historical Directory child row")
        if not refs:
            raise Blocked("PartyHandle", row.pk, "import origin was not retained")
        link_id = next((pk for _, pk in sorted(sources, key=str) if pk is not None), None)
        return ("integration", integration_id), "directory", link_id
    raise Blocked("PartyHandle", row.pk, f"source {row.source!r} has no proven persisted principal")


def _decision(legacy, row, refs, notices):
    """Return the carrier's decision as (principal, kind, rank, snak), or None."""
    if row.is_confirmed and row.is_dismissed:
        raise Blocked("PartyHandle", row.pk, "confirmed and dismissed flags contradict one another")
    if not (row.is_confirmed or row.is_dismissed):
        return None
    snak = "value" if row.is_confirmed else "no_value"
    decider = next((pk for pk in (row.updated_by_id, row.created_by_id) if legacy.person(pk)), None)
    if decider is not None:
        return ("user", decider), "person", "preferred", snak
    recorded = row.updated_by_id or row.created_by_id
    if recorded is not None:
        ref = legacy.user_ref(recorded)
        if ref not in refs:
            refs.append(ref)
    notices["decision by a service account or without a recorded actor: system decision by the fold"] += 1
    return ("seed", SYSTEM_KEY), "seed", "normal", snak


def classify(legacy, row, notices):
    """Return the statements one carrier folds into: (party, handle, refs, link_id, assertions)."""
    party = legacy.rows("parties.Party").filter(pk=row.party_id).first()
    handle = legacy.rows("parties.Handle").filter(pk=row.handle_id).first()
    if party is None or handle is None:
        raise Blocked("PartyHandle", row.pk, "subject or member origin is missing")
    if not isinstance(row.metadata, dict) or not 0 <= row.confidence <= 1:
        raise Blocked("PartyHandle", row.pk, "invalid retained metadata or confidence")
    metadata = _retained_metadata(row, notices)
    evidence = metadata.get("evidence_refs", [])
    if not isinstance(evidence, list):
        raise Blocked("PartyHandle", row.pk, "origin references must be a list")
    refs, origins = [], []
    for ref in evidence:
        origin, ref = legacy.record(row, ref)
        origins.append(origin)
        if ref not in refs:
            refs.append(ref)
    source = _source(legacy, row, metadata, party, handle, refs, origins, notices)
    decision = _decision(legacy, row, refs, notices)
    assertions = []
    if source is not None:
        principal, kind, link_id = source
        assertions.append((principal, kind, "normal", "value", row.confidence, link_id))
    if decision is not None:
        principal, kind, rank, snak = decision
        assertions.append((principal, kind, rank, snak, 1.0, None))
    return party, handle, refs, assertions


def plan(apps, db):
    """Classify every carrier: (legacy, plans, blockers, notices); the preflight reads the last two."""
    legacy = Legacy(apps, db)
    plans, blockers, notices = [], [], Counter()
    for row in legacy.rows("parties.PartyHandle").order_by("pk").iterator():
        try:
            plans.append((row, classify(legacy, row, notices)))
        except Blocked as blocked:
            blockers.append(blocked)
    return legacy, plans, blockers, notices


def _principal(legacy, principal):
    kind, value = principal
    if kind == "user":
        return str(SubjectRef.of(app_settings.REBAC_USER_TYPE, str(value)))
    if kind == "integration":
        return str(SubjectRef.of("integrate/integration", str(value)))
    label, subject_type = ("provenance.Rule", "provenance/rule") if kind == "rule" else ("provenance.Seed", "provenance/seed")
    row, _ = legacy.rows(label).get_or_create(key=value)
    return str(SubjectRef.of(subject_type, str(row.pk)))


def _carry_readers(legacy, row, party):
    """Copy grant data, never inspect schema permissions or evaluate a policy."""
    model = legacy.apps.get_model(app_settings.REBAC_RELATIONSHIP_MODEL)
    rows = legacy.rows(app_settings.REBAC_RELATIONSHIP_MODEL)
    grants = list(rows.filter(resource_type="parties/party_handle", resource_id=str(row.pk),
                             relation__in=("owner", "editor", "reader")).order_by("pk"))
    if row.created_by_id and row.created_by_id in legacy.user_kinds:
        grants.append(model(subject_type=app_settings.REBAC_USER_TYPE, subject_id=str(row.created_by_id)))
    for grant in grants:
        key = dict(resource_type="parties/party", resource_id=str(party.pk), relation="carrier_reader",
                   subject_type=grant.subject_type, subject_id=grant.subject_id,
                   optional_subject_relation=grant.optional_subject_relation, caveat_name=grant.caveat_name)
        existing, created = rows.get_or_create(**key, defaults={
            "caveat_context": grant.caveat_context, "expires_at": grant.expires_at,
        })
        if not created:
            if existing.caveat_context != grant.caveat_context:
                raise Blocked("PartyHandle", row.pk, "Party reader grant has incompatible caveat context")
            if existing.expires_at is not None:
                expires = None if grant.expires_at is None else max(existing.expires_at, grant.expires_at)
                rows.filter(pk=existing.pk).update(expires_at=expires)
    return len(grants)


def forwards(apps, schema_editor):
    db = schema_editor.connection.alias
    legacy, plans, blockers, notices = plan(apps, db)
    if blockers:
        raise ValueError(f"PartyHandle fold: {len(blockers)} rows block this database:\n" + "\n".join(map(str, blockers)))
    if not plans:
        return
    statements = legacy.rows("parties.PartyStatement")
    history = legacy.rows("parties.HistoricalPartyStatement")
    now = timezone.now()
    pending, snapshots = {}, []

    def flush():
        records = list(pending.values())
        created = [row for row in records if row.pk is None]
        existing = [row for row in records if row.pk is not None]
        # bulk_create stamps auto_now(_add) columns through pre_save; the carrier's stamps win.
        stamps = [(row, row.created_at, row.updated_at) for row in created]
        statements.bulk_create(created, batch_size=500)
        for row, created_at, updated_at in stamps:
            row.created_at, row.updated_at = created_at, updated_at
        statements.bulk_update(created, ("created_at", "updated_at"), batch_size=500)
        statements.bulk_update(existing, [field.name for field in statements.model._meta.concrete_fields
                                          if not field.primary_key], batch_size=500)
        history.bulk_create([
            history.model(**(values | {"id": row.pk}), history_date=now, history_type=kind, history_change_reason=REASON)
            for row, values, kind in snapshots
        ], batch_size=500)
        pending.clear()
        snapshots.clear()

    cs_model = apps.get_model(settings.ANGEE_CHANGESET_MODEL)
    with system_context(reason=REASON):
        cs = cs_model._base_manager.using(db).create(activity="system", change_reason=REASON, state="closed", outcome="success")
        count = readers = 0
        winners = {}
        for row, (party, handle, refs, assertions) in plans:
            for principal, source_kind, rank, snak, confidence, link_id in assertions:
                asserted_by = _principal(legacy, principal)
                key = dict(subject_id=row.party_id, handle_id=row.handle_id, aspect="handle", asserted_by=asserted_by)
                identity = tuple(key.values())
                existing = pending.get(identity) or statements.filter(**key, superseded_id=None).first()
                if existing is not None and existing.changeset_id != cs.pk:
                    raise Blocked("PartyHandle", row.pk, f"statement key {asserted_by} already contains newer evidence")
                values = dict(source_kind=source_kind, rank=rank, snak=snak, confidence=confidence, evidence=refs,
                              link_id=link_id, changeset_id=cs.pk, status="asserted", applied=False)
                created = existing is None
                statement = statements.model(**key, superseded_id=None) if created else existing
                for name, value in values.items():
                    setattr(statement, name, value)
                statement.created_at = row.created_at if created else statement.created_at
                statement.updated_at = row.updated_at if created else now
                pending[identity] = statement
                snapshot = {field.attname: getattr(statement, field.attname) for field in statement._meta.concrete_fields}
                snapshots.append((statement, snapshot, "+" if created else "~"))
                count += 1
                if rank == "preferred" or confidence >= REVIEW_CONFIDENCE:
                    group = (row.party_id, row.handle_id)
                    order = (rank == "preferred", confidence, row.updated_at, row.pk)
                    if group not in winners or order > winners[group][0]:
                        winners[group] = (order, statement)
            readers += _carry_readers(legacy, row, party)
            if len(pending) >= 500:
                flush()
        flush()
        decisive = []
        for (party_id, handle_id), (_, statement) in winners.items():
            # Another fold (a consumer's own) may already have applied this pair: it keeps it.
            if statements.filter(subject_id=party_id, handle_id=handle_id, aspect="handle", superseded_id=None,
                                 applied=True).exclude(pk=statement.pk).exists():
                notices["handle: another fold's statement is applied; recorded beside it"] += 1
                continue
            decisive.append(statement)
        for batch in batched(decisive, 500):
            for statement in batch:
                statement.applied = True
            statements.bulk_update(batch, ("applied",), batch_size=500)
            history.bulk_create([
                history.model(**{field.attname: getattr(statement, field.attname) for field in statement._meta.concrete_fields},
                              history_date=now, history_type="~", history_change_reason=REASON)
                for statement in batch
            ], batch_size=500)
            count += len(batch)
        cs_model._base_manager.using(db).filter(pk=cs.pk).update(
            counts={"parties.partystatement": count}, touched=["parties.partystatement"],
        )
        print(f"PartyHandle fold: {len(plans)} carriers, {count} statement snapshots; "
              f"{readers} carrier grants copied to read only.")
        for reason, number in sorted(notices.items()):
            print(f"PartyHandle fold: {number} x {reason}")


class Migration(migrations.Migration):
    dependencies = [
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
        migrations.swappable_dependency(settings.ANGEE_CHANGESET_MODEL),
        ("provenance", "__latest__"), ("rebac", "__latest__"),
    ]
    operations = [migrations.RunPython(forwards, migrations.RunPython.noop)]
