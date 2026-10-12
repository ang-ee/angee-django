"""Retire folded handle carriers after checking retained source/decision rows.

The fold precedes this declaration and refuses unresolved attribution. Each old
pair must retain the fold's source assertion in native history (or, for an import
carrier the fold leaves unasserted, none of its own: another fold's, whatever its
reason, or no assertion at all); every current statement of the pair must be in
native history; recorded decisions must have the corresponding stance; every
resolved Handle pointer must have an applied value statement, whichever fold wrote it.

Retained workflow/decision JSON references to a carrier change to the folded
statement id (``party_handle_id`` becomes ``statement_id``); carrier record
references change to the Party and keep Handle references. Generic record edges
(the explicit list below, the pointing models known when this migration was
written) are retargeted from the carrier to its Party; a row that would collide
with an existing Party edge under the edge's unique constraint is dropped, a
pending StepWatch folding its flag into the survivor. References to carriers that
no longer exist are named and left as they are (a JSON ``party_handle_id`` is
blanked): they already dangled. Carrier permission tuples and default
``auth.Permission`` rows leave with the resource. Frozen pre-cutover identity hashes
intentionally fail the owner comparison and require fresh human review.

Legacy carrier metadata beyond typed origin references, audit timestamps and the
collapsed source label leave with the carrier; statement/history evidence and
decision attribution remain.

Reverse recreates an empty legacy table through Django and nothing else: it does
not undo the JSON rewrites, the edge retargets, the deleted grants or permissions,
and it does not invent carriers from several independent sources. The pre-cutover
code rejects the rewritten ``statement_id`` (``ProposedHandle`` forbids extra
keys), so reversing requires reverting the parties fold chain as a whole.
"""

from django.conf import settings
from django.db import migrations, models
from rebac import system_context
from sqids import Sqids
from sqids.constants import DEFAULT_ALPHABET, DEFAULT_MIN_LENGTH

REASON = "retire.party_handle"
CARRIER_PREFIX, PARTY_PREFIX, STATEMENT_PREFIX = "phl_", "pty_", ""
EDGES = (
    # (model label, content type field, object id field): every RecordRefMixin edge at authoring time.
    ("integrate.RecordLink", "target_content_type", "target_object_id"),
    (settings.ANGEE_CHANGESET_MODEL, "origin_content_type", "origin_object_id"),
    ("workflows.WorkflowRun", "subject_content_type", "subject_object_id"),
    ("workflows.StepRecord", "content_type", "object_id"),
    ("workflows.StepWatch", "content_type", "object_id"),
    ("workflows.TriggerEvent", "record_content_type", "record_object_id"),
    ("projects.ProjectBinding", "content_type", "object_id"),
    ("projects.Link", "content_type", "object_id"),
    ("storage.FileAttachment", "content_type", "object_id"),
    ("tags.TagAssignment", "content_type", "object_id"),
    ("knowledge.RecordBinding", "content_type", "object_id"),
    ("money.CurrencyRate", "context_content_type", "context_object_id"),
    ("portfolio.Update", "content_type", "object_id"),
    ("messaging.ThreadAttachment", "content_type", "object_id"),
    ("decisions.DecisionRecord", "content_type", "object_id"),
    ("extraction.ExtractionSource", "content_type", "object_id"),
)


def applies(state):
    if ("parties", "partyhandle") not in state.models:
        return False
    # The aspect fold reads the carriers to classify imported parties; it precedes this retirement.
    required = {("parties", "partystatement"), ("parties", "historicalpartystatement"), ("parties", "partysameaspairstatement")}
    return required.issubset(state.models)


def _rows(apps, label, db):
    # Historical state has physical fields only; native sqid ordering is virtual.
    try:
        model = apps.get_model(label)
    except LookupError:
        return None
    return model._base_manager.using(db).order_by()


def forwards(apps, schema_editor):
    db = schema_editor.connection.alias
    if schema_editor.connection.vendor == "postgresql":
        schema_editor.execute("SET CONSTRAINTS ALL IMMEDIATE")
    codec = Sqids(min_length=getattr(settings, "DJANGO_SQIDS_MIN_LENGTH", None) or DEFAULT_MIN_LENGTH,
                  alphabet=getattr(settings, "DJANGO_SQIDS_ALPHABET", None) or DEFAULT_ALPHABET)

    def public_id(prefix, pk):
        return f"{prefix}{codec.encode([int(pk)])}"

    carriers = _rows(apps, "parties.PartyHandle", db)
    statements = _rows(apps, "parties.PartyStatement", db)
    history = _rows(apps, "parties.HistoricalPartyStatement", db)
    problems, notices = [], []
    mapping, by_pk = {}, {}
    for row in carriers.order_by("pk").iterator():
        pair = dict(subject_id=row.party_id, handle_id=row.handle_id, aspect="handle")
        rows = statements.filter(**pair, superseded_id=None)
        kept = rows.order_by("-rank", "-confidence", "-updated_at", "-pk").first()
        if kept is None or not history.filter(**pair, history_change_reason="fold.party_handle").exists():
            # The fold asserts every carrier except an import it leaves unasserted; another
            # fold (a consumer's own) may assert that one under its own reason.
            if row.is_confirmed or row.is_dismissed or row.source != "import":
                problems.append(f"PartyHandle {row.pk}: missing folded source or history")
            elif kept is None:
                notices.append(f"PartyHandle {row.pk}: unasserted import carrier retires without a statement")
            else:
                notices.append(f"PartyHandle {row.pk}: unasserted import carrier is asserted by another fold")
            by_pk[row.pk] = row.party_id
            mapping[public_id(CARRIER_PREFIX, row.pk)] = (
                public_id(STATEMENT_PREFIX, kept.pk) if kept is not None else "", public_id(PARTY_PREFIX, row.party_id),
            )
            continue
        if not all(history.filter(id=pk).exists() for pk in rows.values_list("pk", flat=True)):
            problems.append(f"PartyHandle {row.pk}: missing folded source or history")
            continue
        snak = "no_value" if row.is_dismissed else "value"
        if (row.is_dismissed or row.is_confirmed) and not rows.filter(snak=snak).filter(
            models.Q(rank="preferred", source_kind="person") | models.Q(source_kind="seed"),
        ).exists():
            problems.append(f"PartyHandle {row.pk}: missing folded decision stance")
            continue
        mapping[public_id(CARRIER_PREFIX, row.pk)] = (public_id(STATEMENT_PREFIX, kept.pk), public_id(PARTY_PREFIX, row.party_id))
        by_pk[row.pk] = row.party_id
    for handle in _rows(apps, "parties.Handle", db).filter(party_id__isnull=False).iterator():
        if not statements.filter(subject_id=handle.party_id, handle_id=handle.pk, aspect="handle",
                                 superseded_id=None, status="asserted", applied=True, snak="value").exists():
            problems.append(f"Handle {handle.pk} (party={handle.party_id}): no applied folded statement")
    if problems:
        raise ValueError(f"PartyHandle retirement: {len(problems)} rows block this database:\n" + "\n".join(problems))

    def rewrite(value, holder):
        if isinstance(value, list):
            return [rewrite(item, holder) for item in value]
        if not isinstance(value, dict):
            return value
        if isinstance(value.get("model"), str) and value["model"].lower() == "parties.partyhandle":
            old = str(value.get("id", ""))
            if old not in mapping:
                notices.append(f"{holder}: retained reference to deleted PartyHandle {old} left as it is")
                return value
            return {**value, "model": "parties.Party", "id": mapping[old][1]}
        result = {}
        for key, item in value.items():
            if key == "party_handle_id":
                if item and item not in mapping:
                    notices.append(f"{holder}: party_handle_id {item} names a deleted PartyHandle; blanked")
                result["statement_id"] = mapping[item][0] if item in mapping else ""
            elif key in mapping:
                # Old identity-review actions addressed a carrier flag; the
                # workflow applies its reviewed choice through Party.apply_identity.
                if item != {"fields": {"is_confirmed": {}}}:
                    problems.append(f"{holder}: retained PartyHandle action {key} is not a confirm flag")
                result.setdefault(mapping[key][1], {"fields": {}})
            else:
                result[key] = rewrite(item, holder)
        if result.get("handle_id") and result.get("id") in mapping:
            result["id"] = mapping[result["id"]][0]
        return result

    with system_context(reason=REASON):
        for label, columns in (
            ("workflows.WorkflowRun", ("input", "output")),
            ("workflows.StepRun", ("input", "output", "state", "notes")),
            ("decisions.Decision", ("context", "proposal", "verdict_values")),
        ):
            rows = _rows(apps, label, db)
            if rows is None:
                continue
            for row in rows.order_by("pk").iterator():
                values = {name: rewrite(getattr(row, name), f"{label} {row.pk} column {name}") for name in columns}
                changed = {name: value for name, value in values.items() if value != getattr(row, name)}
                if changed and not problems:
                    rows.filter(pk=row.pk).update(**changed)
        if problems:
            raise ValueError(f"PartyHandle retirement: {len(problems)} references block this database:\n" + "\n".join(problems))
        content_types = _rows(apps, "contenttypes.ContentType", db)
        ct = content_types.filter(app_label="parties", model="partyhandle").first()
        if ct is not None:
            party_ct, _ = content_types.get_or_create(app_label="parties", model="party")
            for label, ct_name, object_name in EDGES:
                rows = _rows(apps, label, db)
                if rows is None:
                    continue
                model = rows.model
                ct_column = model._meta.get_field(ct_name).attname
                fields = {field.name: field for field in model._meta.fields}
                for row in rows.filter(**{ct_column: ct.pk}).order_by("pk").iterator():
                    try:
                        old_pk = int(getattr(row, object_name))
                    except (TypeError, ValueError):
                        old_pk = None
                    if old_pk not in by_pk:
                        notices.append(f"{label} {row.pk}: points at deleted PartyHandle {getattr(row, object_name)!r}; left as it is")
                        continue
                    key = {ct_column: party_ct.pk, object_name: str(by_pk[old_pk])}
                    lookup = dict(key)
                    for constraint in model._meta.constraints:
                        if isinstance(constraint, models.UniqueConstraint) and {ct_name, object_name}.issubset(constraint.fields):
                            lookup.update({column: getattr(row, fields[column].attname) for column in constraint.fields
                                           if column not in (ct_name, object_name)})
                    other = rows.filter(**lookup).exclude(pk=row.pk).first()
                    if other is not None and len(lookup) > len(key):
                        if label == "workflows.StepWatch" and row.pending and not other.pending:
                            rows.filter(pk=other.pk).update(pending=True)
                        rows.filter(pk=row.pk).delete()
                    else:
                        rows.filter(pk=row.pk).update(**key)
            permissions = _rows(apps, "auth.Permission", db)
            if permissions is not None:
                permissions.filter(content_type_id=ct.pk).delete()
        for label, resource, subject in (
            ("Relationship", "resource_type", "subject_type"),
            ("RelationshipRegistry", "resource_fk__resource_type", "subject_fk__resource_type"),
        ):
            rows = _rows(apps, "rebac." + label, db)
            rows.filter(models.Q(**{resource: "parties/party_handle"}) | models.Q(**{subject: "parties/party_handle"})).delete()
    for notice in notices:
        print(f"PartyHandle retirement: {notice}")


class Migration(migrations.Migration):
    dependencies = [("rebac", "__latest__"), ("contenttypes", "0002_remove_content_type_name")]
    operations = [
        migrations.RunPython(forwards, migrations.RunPython.noop),
        migrations.DeleteModel(name="PartyHandle"),
    ]
