"""Retire folded handle carriers after checking retained source/decision rows.

The fold precedes this declaration and refuses unresolved attribution. Each old
pair must retain its normal source assertion in native history; recorded decisions
must have the corresponding preferred person stance. Every resolved Handle
pointer must have an applied value statement.

Legacy carrier metadata beyond typed origin references, audit timestamps and
the collapsed source label leave with the carrier; statement/history evidence
and decision attribution remain. Reverse recreates an empty legacy table via
Django; it does not invent carriers from several independent sources.

Retained workflow JSON references change to statement IDs; concern/evidence
references change to the Party and keep Handle references. Frozen pre-cutover
identity hashes intentionally fail the owner comparison and require fresh human
review. Generic pointers not covered here block retirement rather than dangle.
"""

from django.apps import apps as live_apps
from django.db import migrations, models
from rebac import system_context

from angee.base.identity import SqidPublicIdentity, public_id_for
from angee.base.merge import EdgeReference, references


def applies(state):
    if ("parties", "partyhandle") not in state.models:
        return False
    required = {("parties", "partystatement"), ("parties", "historicalpartystatement")}
    return required.issubset(state.models)


def forwards(apps, schema_editor):
    db = schema_editor.connection.alias
    if schema_editor.connection.vendor == "postgresql":
        schema_editor.execute("SET CONSTRAINTS ALL IMMEDIATE")
    carriers = apps.get_model("parties", "PartyHandle")._base_manager.using(db).order_by()
    statements = apps.get_model("parties", "PartyStatement")._base_manager.using(db).order_by()
    history = apps.get_model("parties", "HistoricalPartyStatement")._base_manager.using(db).order_by()
    old_identity = SqidPublicIdentity(prefix="phl_")
    statement_identity = live_apps.get_model("parties", "PartyStatement")
    party_identity = live_apps.get_model("parties", "Party")
    mapping, by_pk = {}, {}
    for row in carriers.order_by("pk").iterator():
        rows = statements.filter(subject_id=row.party_id, handle_id=row.handle_id, aspect="handle", superseded_id=None)
        normal = history.filter(subject_id=row.party_id, handle_id=row.handle_id, aspect="handle",
                                rank="normal", confidence=row.confidence, history_change_reason="fold.party_handle")
        if not normal.exists() or not rows.exists() or not all(history.filter(id=pk).exists() for pk in rows.values_list("pk", flat=True)):
            raise ValueError(f"PartyHandle {row.pk}: missing folded source or history")
        decision = rows.filter(rank="preferred", source_kind="person",
                               snak="no_value" if row.is_dismissed else "value")
        if (row.is_dismissed or row.is_confirmed) and not decision.exists():
            raise ValueError(f"PartyHandle {row.pk}: missing folded person decision")
        kept = rows.order_by("-rank", "-confidence", "-updated_at", "-pk").first()
        value = (public_id_for(statement_identity, kept.pk), public_id_for(party_identity, row.party_id))
        mapping[old_identity.public_id_from_pk(row.pk)] = value
        by_pk[row.pk] = row.party_id
    handles = apps.get_model("parties", "Handle")._base_manager.using(db).order_by()
    for handle in handles.filter(party_id__isnull=False).iterator():
        if not statements.filter(subject_id=handle.party_id, handle_id=handle.pk, aspect="handle",
                                 superseded_id=None, status="asserted", applied=True, snak="value").exists():
            raise ValueError(f"Handle {handle.pk} (party={handle.party_id}): no applied folded statement")

    def rewrite(value):
        if isinstance(value, list):
            return [rewrite(item) for item in value]
        if not isinstance(value, dict):
            return value
        if isinstance(value.get("model"), str) and value["model"].lower() == "parties.partyhandle":
            old = str(value.get("id", ""))
            if old not in mapping:
                raise ValueError(f"Retained PartyHandle reference {old}: no folded statement")
            return {**value, "model": "parties.Party", "id": mapping[old][1]}
        result = {}
        for key, item in value.items():
            if key == "party_handle_id":
                if item and item not in mapping:
                    raise ValueError(f"Retained PartyHandle reference {item}: no folded statement")
                result["statement_id"] = mapping[item][0] if item else ""
            elif key in mapping:
                # Old identity-review actions addressed a carrier flag; the
                # workflow applies its reviewed choice through Party.apply_identity.
                target = mapping[key][1]
                if item != {"fields": {"is_confirmed": {}}}:
                    raise ValueError(f"Retained PartyHandle action {key}: unsupported action")
                result.setdefault(target, {"fields": {}})
            else:
                result[key] = rewrite(item)
        if result.get("handle_id") and result.get("id") in mapping:
            result["id"] = mapping[result["id"]][0]
        return result

    with system_context(reason="retire.party_handle"):
        for label, columns in (
            ("workflows.WorkflowRun", ("input", "output")),
            ("workflows.StepRun", ("input", "output", "state", "notes")),
            ("decisions.Decision", ("context", "proposal", "verdict_values")),
        ):
            try:
                model = apps.get_model(label)
            except LookupError:
                continue
            for row in model._base_manager.using(db).order_by("pk").iterator():
                values = {name: rewrite(getattr(row, name)) for name in columns}
                changed = {name: value for name, value in values.items() if value != getattr(row, name)}
                if changed:
                    model._base_manager.using(db).filter(pk=row.pk).update(**changed)
        ct = apps.get_model("contenttypes", "ContentType")._base_manager.using(db).filter(
            app_label="parties", model="partyhandle",
        ).first()
        if ct is not None:
            party_ct, _ = apps.get_model("contenttypes", "ContentType")._base_manager.using(db).get_or_create(
                app_label="parties", model="party",
            )
            for reference in references(carriers.model):
                model = apps.get_model(reference.model._meta.label)
                if not isinstance(reference, EdgeReference):
                    if model._base_manager.using(db).exists():
                        raise ValueError(f"{reference.label}: unsupported retired carrier reference")
                    continue
                name, object_name = reference.columns
                fields = {field.name: field for field in model._meta.fields}
                rows = model._base_manager.using(db).filter(**{name: ct.pk}).order_by("pk")
                for row in rows.iterator():
                    old_pk = int(getattr(row, object_name))
                    if old_pk not in by_pk:
                        raise ValueError(f"{model._meta.label} {row.pk}: unresolved retired handle pointer")
                    key = {name: party_ct.pk, object_name: by_pk[old_pk]}
                    lookup = dict(key)
                    for constraint in model._meta.constraints:
                        if isinstance(constraint, models.UniqueConstraint) and {name, object_name}.issubset(constraint.fields):
                            lookup.update({column: getattr(row, fields[column].attname) for column in constraint.fields
                                           if column not in key})
                    other = rows.model._base_manager.using(db).filter(**lookup).exclude(pk=row.pk).first()
                    if other is not None and len(lookup) > len(key):
                        if model._meta.label_lower == "workflows.stepwatch" and row.pending and not other.pending:
                            model._base_manager.using(db).filter(pk=other.pk).update(pending=True)
                        model._base_manager.using(db).filter(pk=row.pk).delete()
                    else:
                        model._base_manager.using(db).filter(pk=row.pk).update(**key)

        for label, resource, subject in (
            ("Relationship", "resource_type", "subject_type"),
            ("RelationshipRegistry", "resource_fk__resource_type", "subject_fk__resource_type"),
        ):
            rows = apps.get_model("rebac", label)._base_manager.using(db).order_by()
            rows.filter(models.Q(**{resource: "parties/party_handle"}) | models.Q(**{subject: "parties/party_handle"})).delete()


class Migration(migrations.Migration):
    dependencies = [("rebac", "__latest__")]
    operations = [
        migrations.RunPython(forwards, migrations.RunPython.noop),
        migrations.DeleteModel(name="PartyHandle"),
    ]
