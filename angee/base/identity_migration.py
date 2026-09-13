"""One-shot migration of stored REBAC model references to primary-key ids."""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import ImproperlyConfigured, ValidationError
from django.db import connections, transaction
from rebac.models import RebacResource, Relationship, RelationshipRegistry
from rebac.resources import model_for_resource_type


@dataclass
class RebacIdMigrationPlan:
    """A fully validated set of physical row changes."""

    row_deletes: list[Any] = field(default_factory=list)
    row_updates: list[tuple[Any, dict[str, Any]]] = field(default_factory=list)
    resource_deletes: list[Any] = field(default_factory=list)
    resource_updates: list[tuple[Any, dict[str, Any]]] = field(default_factory=list)
    references: int = 0

    @property
    def changes(self) -> int:
        return len(self.row_deletes) + len(self.row_updates) + len(self.resource_deletes) + len(
            self.resource_updates
        )

    def apply(self) -> None:
        """Apply the preflighted changes in constraint-safe order."""

        for row in self.row_deletes:
            row.delete()
        for row, values in self.row_updates:
            for name, value in values.items():
                setattr(row, name, value)
            row.save(update_fields=tuple(values))
        for resource in self.resource_deletes:
            resource.delete()
        for resource, values in self.resource_updates:
            for name, value in values.items():
                setattr(resource, name, value)
            resource.save(update_fields=tuple(values))


def plan_rebac_id_migration(*, using: str = "default") -> RebacIdMigrationPlan:
    """Return a complete collision-checked plan without changing the database."""

    connection = connections[using]
    tables = set(connection.introspection.table_names())
    required = {
        Relationship._meta.db_table,
        RebacResource._meta.db_table,
        RelationshipRegistry._meta.db_table,
    }
    present = required & tables
    if not present:
        return RebacIdMigrationPlan()
    if present != required:
        raise ImproperlyConfigured(
            "REBAC id migration requires both relationship stores and the resource registry"
        )

    resources = list(RebacResource._base_manager.using(using).order_by("pk"))
    denormalized = list(Relationship._base_manager.using(using).order_by("pk"))
    registry_rows = list(
        RelationshipRegistry._base_manager.using(using)
        .select_related("resource_fk", "subject_fk")
        .order_by("pk")
    )
    refs = {(row.resource_type, row.resource_id) for row in resources}
    refs.update((row.resource_type, row.resource_id) for row in denormalized)
    refs.update((row.subject_type, row.subject_id) for row in denormalized)
    resource_by_ref = {(row.resource_type, row.resource_id): row for row in resources}
    canonical = {
        ref: _canonical_ref(ref, resource_by_ref.get(ref), using=using)
        for ref in sorted(refs)
    }
    plan = RebacIdMigrationPlan(references=len(refs))
    _plan_denormalized(plan, denormalized, canonical)
    resource_targets = _plan_resources(plan, resources, canonical)
    _plan_registry(plan, registry_rows, resource_targets)
    return plan


def migrate_rebac_ids(*, using: str = "default") -> RebacIdMigrationPlan:
    """Preflight and atomically apply the primary-key identity cutover."""

    with transaction.atomic(using=using):
        plan = plan_rebac_id_migration(using=using)
        plan.apply()
    return plan


def _canonical_ref(ref: tuple[str, str], resource: Any | None, *, using: str) -> tuple[str, str]:
    resource_type, old_id = ref
    model = model_for_resource_type(resource_type)
    if old_id == "*" or model is None or not model._meta.managed:
        return ref

    candidates: set[str] = set()
    manager = model._base_manager.using(using)
    try:
        direct = manager.filter(pk=old_id).values_list("pk", flat=True).first()
    except (TypeError, ValueError, ValidationError):
        direct = None
    if direct is not None:
        candidates.add(str(direct))

    legacy_lookup = getattr(model, "legacy_rebac_id_lookup", None)
    if callable(legacy_lookup):
        try:
            legacy = manager.filter(**legacy_lookup(old_id)).values_list("pk", flat=True).first()
        except (TypeError, ValueError, ValidationError):
            legacy = None
        if legacy is not None:
            candidates.add(str(legacy))

    if resource is not None and (resource.content_type_id is not None or resource.object_pk):
        concrete = model._meta.concrete_model or model
        expected_id = (
            ContentType.objects.db_manager(using)
            .filter(app_label=concrete._meta.app_label, model=concrete._meta.model_name)
            .values_list("pk", flat=True)
            .first()
        )
        if resource.content_type_id != expected_id or not resource.object_pk:
            raise ImproperlyConfigured(
                f"REBAC resource {resource_type}:{old_id} has a mismatched Django backing pointer"
            )
        try:
            pointed = (
                manager.filter(pk=resource.object_pk)
                .values_list("pk", flat=True)
                .first()
            )
        except (TypeError, ValueError, ValidationError) as error:
            raise ImproperlyConfigured(
                f"REBAC resource {resource_type}:{old_id} has an invalid object_pk pointer"
            ) from error
        if pointed is None:
            raise ImproperlyConfigured(
                f"REBAC resource {resource_type}:{old_id} points at a missing Django row"
            )
        candidates.add(str(pointed))

    if len(candidates) != 1:
        reason = "ambiguous" if candidates else "unresolvable"
        raise ImproperlyConfigured(f"REBAC model reference {resource_type}:{old_id} is {reason}")
    return resource_type, candidates.pop()


def _condition(row: Any) -> tuple[str, Any]:
    return json.dumps(row.caveat_context, sort_keys=True), row.expires_at


def _choose_rows(
    groups: dict[tuple[Any, ...], list[Any]],
    *,
    label: str,
) -> tuple[list[Any], dict[Any, int]]:
    deletes: list[Any] = []
    xids: dict[Any, int] = {}
    for rows in groups.values():
        conditions = [_condition(row) for row in rows]
        if any(condition != conditions[0] for condition in conditions[1:]):
            raise ImproperlyConfigured(
                f"REBAC {label} collision has different caveat context or expiry"
            )
        survivor = max(rows, key=lambda row: (row.written_at_xid, -row.pk))
        deletes.extend(row for row in rows if row.pk != survivor.pk)
        xids[survivor] = max(row.written_at_xid for row in rows)
    return deletes, xids


def _plan_denormalized(
    plan: RebacIdMigrationPlan,
    rows: list[Any],
    canonical: dict[tuple[str, str], tuple[str, str]],
) -> None:
    groups: dict[tuple[Any, ...], list[Any]] = {}
    targets: dict[Any, tuple[str, str]] = {}
    for row in rows:
        resource = canonical[(row.resource_type, row.resource_id)]
        subject = canonical[(row.subject_type, row.subject_id)]
        targets[row] = (resource[1], subject[1])
        key = (
            resource[0],
            resource[1],
            row.relation,
            subject[0],
            subject[1],
            row.optional_subject_relation,
            row.caveat_name,
        )
        groups.setdefault(key, []).append(row)
    deletes, xids = _choose_rows(groups, label="denormalized tuple")
    plan.row_deletes.extend(deletes)
    deleted = {row.pk for row in deletes}
    for row, (resource_id, subject_id) in targets.items():
        if row.pk in deleted:
            continue
        values: dict[str, Any] = {}
        if row.resource_id != resource_id:
            values["resource_id"] = resource_id
        if row.subject_id != subject_id:
            values["subject_id"] = subject_id
        if row.written_at_xid != xids[row]:
            values["written_at_xid"] = xids[row]
        if values:
            plan.row_updates.append((row, values))


def _plan_resources(
    plan: RebacIdMigrationPlan,
    resources: list[Any],
    canonical: dict[tuple[str, str], tuple[str, str]],
) -> dict[int, Any]:
    groups: dict[tuple[str, str], list[Any]] = {}
    for row in resources:
        groups.setdefault(canonical[(row.resource_type, row.resource_id)], []).append(row)
    targets: dict[int, Any] = {}
    for target_ref, rows in groups.items():
        existing = next(
            (row for row in rows if (row.resource_type, row.resource_id) == target_ref),
            rows[0],
        )
        pointers = {
            (row.content_type_id, row.object_pk)
            for row in rows
            if row.content_type_id is not None
        }
        if len(pointers) > 1:
            raise ImproperlyConfigured(
                f"REBAC resource collision for {target_ref!r} has conflicting pointers"
            )
        pointer = next(iter(pointers), None)
        for row in rows:
            targets[row.pk] = existing
            if row.pk != existing.pk:
                plan.resource_deletes.append(row)
        values: dict[str, Any] = {}
        if (existing.resource_type, existing.resource_id) != target_ref:
            values["resource_id"] = target_ref[1]
        if pointer is not None and existing.content_type_id is None:
            values.update(content_type_id=pointer[0], object_pk=pointer[1])
        if values:
            plan.resource_updates.append((existing, values))
    return targets


def _plan_registry(
    plan: RebacIdMigrationPlan,
    rows: list[Any],
    resource_targets: dict[int, Any],
) -> None:
    groups: dict[tuple[Any, ...], list[Any]] = {}
    targets: dict[Any, tuple[Any, Any]] = {}
    for row in rows:
        resource = resource_targets[row.resource_fk_id]
        subject = resource_targets[row.subject_fk_id]
        targets[row] = (resource, subject)
        key = (
            resource.pk,
            row.relation,
            subject.pk,
            row.optional_subject_relation,
            row.caveat_name,
        )
        groups.setdefault(key, []).append(row)
    deletes, xids = _choose_rows(groups, label="registry tuple")
    plan.row_deletes.extend(deletes)
    deleted = {row.pk for row in deletes}
    for row, (resource, subject) in targets.items():
        if row.pk in deleted:
            continue
        values = {}
        if row.resource_fk_id != resource.pk:
            values["resource_fk_id"] = resource.pk
        if row.subject_fk_id != subject.pk:
            values["subject_fk_id"] = subject.pk
        if row.written_at_xid != xids[row]:
            values["written_at_xid"] = xids[row]
        if values:
            plan.row_updates.append((row, values))
