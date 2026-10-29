"""Materialize declarative grant fixtures into REBAC relationship tuples.

A ``kind = "grants"`` resource entry (``entries.GRANT_KIND``) carries flat
``{resource, relation, subject}`` rows in ``resource <- relation <- subject``
direction. This module resolves each row into the REBAC-owned grant tuple shape
(:class:`rebac.RelationshipTuple`) and commits it through
:func:`rebac.write_relationships`, which upserts by the natural tuple key — so a
re-load never duplicates a grant (idempotent, caveat-free for v1).

Reference resolution composes the two owners, never re-deriving either:

* a **literal REBAC ref** — a const anchor / role membership / typed wildcard —
  is written with a slash-form type token (``<ns>/type:<id>[#relation]``, e.g.
  ``angee/role:admin``, ``products/role:products_manager#member``,
  ``auth/user:*``). Subject literals cross a transport boundary, so model-backed
  public ids are normalized through :func:`angee.base.identity.canonical_subject_ref`;
* a **row xref** — anything else (the loader's ``<addon>.<xref>`` form, e.g.
  ``iam.alice``) — resolves through the resource ledger
  (:func:`angee.resources.widgets.resolve_xref`) to the loaded row, whose own
  REBAC identity (:func:`rebac.to_object_ref` / :func:`rebac.to_subject_ref`)
  gives the tuple side — so the row owns its resource type, never a fixture
  prefix. On the resource side a grant honors the row's IS-A: an MTI child
  materializes its own tuple and expands to REBAC-registered concrete parents
  that accept the same relation and subject as a stored grant. This reaches
  parent-typed foreign keys without copying child-only roles into parent schemas.
  The explicitly requested tuple still passes through native REBAC validation;
* the bare ``*`` subject is the public wildcard (the anonymous subject).
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Mapping
from contextlib import nullcontext
from itertools import batched
from typing import TYPE_CHECKING

from django.db import connections, models
from rebac import (
    ObjectRef,
    RelationshipTuple,
    SubjectRef,
    anonymous_actor,
    backend,
    database_context,
    to_subject_ref,
    write_relationships,
)
from rebac.models import active_relationship_model
from rebac.models.relationship import WIRE_VALUE_FIELDS
from rebac.schema.walker import find_relation, subject_allowed_by_relation

from angee.base.identity import canonical_subject_ref
from angee.base.refs import ancestor_object_refs
from angee.resources.entries import GrantGroup, GrantRow
from angee.resources.exceptions import ResourceLoadError
from angee.resources.widgets import resolve_xref

if TYPE_CHECKING:
    from angee.resources.models import Resource


def materialize_grant_groups(
    groups: Iterable[GrantGroup],
    *,
    ledger_model: type[Resource],
    addon_aliases: Mapping[str, str],
    using: str | None = None,
) -> tuple[int, int]:
    """Write every group's grant tuples idempotently; return ``(created, skipped)``.

    Rows are deduplicated by their natural tuple key before writing, so a repeat
    within one load — or a re-load of an unchanged fixture — is a no-op.
    """

    with database_context(using) if using is not None else nullcontext():
        resolved: dict[tuple[str, ...], RelationshipTuple] = {}
        for group in groups:
            for row in group.rows:
                for grant in _grant_tuples(row, ledger_model, addon_aliases, using=using):
                    resolved.setdefault(grant.canonical_key(), grant)
        if not resolved:
            return 0, 0

        existing = _existing_grant_keys(resolved.values(), using=using)
        new_grants = [grant for key, grant in resolved.items() if key not in existing]
        skipped = len(resolved) - len(new_grants)
        # Drive the write off the existence check so an unchanged re-load is a true
        # no-op: only tuples that are actually missing are written, so a repeat load
        # touches no rows (no audit entry, no zookie bump) instead of re-upserting
        # every grant. ``write_relationships`` still upserts, so a concurrent insert
        # between the check and the write stays idempotent.
        if new_grants:
            write_relationships(new_grants)
        return len(new_grants), skipped


def _grant_tuples(
    row: GrantRow,
    ledger_model: type[Resource],
    addon_aliases: Mapping[str, str],
    *, using: str | None = None,
) -> list[RelationshipTuple]:
    """Return the explicit grant and compatible grants on its MTI ancestors.

    Literal refs and plain rows yield exactly one tuple. Ancestor expansion
    follows the native schema's allowed subjects and relation backing. Never
    filter the explicit tuple: invalid requests must fail at the native writer.
    """

    subject = _resolve_subject(row, ledger_model, addon_aliases, using=using)
    own, *ancestors = _resolve_resource_refs(row, ledger_model, addon_aliases, using=using)
    tuples = [RelationshipTuple(resource=own, relation=row.relation, subject=subject)]
    if ancestors:
        schema = backend().schema()
        for resource in ancestors:
            definition = schema.get_definition(resource.resource_type)
            relation = find_relation(definition, row.relation) if definition is not None else None
            if (
                relation is not None
                and not relation.has_backing(resource.resource_id)
                and subject_allowed_by_relation(relation, subject, caveat_name="")
            ):
                tuples.append(RelationshipTuple(resource=resource, relation=row.relation, subject=subject))
    return tuples


def _resolve_resource_refs(
    row: GrantRow,
    ledger_model: type[Resource],
    addon_aliases: Mapping[str, str],
    *, using: str | None = None,
) -> list[ObjectRef]:
    """Resolve the grant resource to every REBAC identity it carries.

    A literal ref names exactly one identity. A row xref resolves through the
    ledger to the loaded row and expands to each REBAC identity the row IS-A — the
    MTI enumeration owned by :func:`angee.base.refs.ancestor_object_refs`.
    The caller filters implicit ancestor grants against the native schema.
    """

    value = row.resource
    if _is_literal_ref(value):
        return [ObjectRef.parse(value)]
    resolved = _resolve_row(row, value, ledger_model, addon_aliases, using=using)
    return list(ancestor_object_refs(resolved))


def _resolve_subject(
    row: GrantRow,
    ledger_model: type[Resource],
    addon_aliases: Mapping[str, str],
    *, using: str | None = None,
) -> SubjectRef:
    """Resolve the grant subject to a subject reference."""

    value = row.subject
    if value == "*":
        return anonymous_actor()
    if _is_literal_ref(value):
        return canonical_subject_ref(value)
    return to_subject_ref(_resolve_row(row, value, ledger_model, addon_aliases, using=using))


def _resolve_row(
    row: GrantRow,
    value: str,
    ledger_model: type[Resource],
    addon_aliases: Mapping[str, str],
    *, using: str | None = None,
) -> models.Model:
    """Resolve one row xref through the ledger, or raise with the grant location."""

    try:
        return resolve_xref(value, ledger_model, addon_aliases, using=using)
    except ValueError as error:
        raise ResourceLoadError(f"{row.entry.display} grant {row.index}: {error}") from error


def _existing_grant_keys(grants: Iterable[RelationshipTuple], *, using: str | None = None) -> set[tuple[str, ...]]:
    """Read native wire identities once per resource-type batch, in either store."""

    resources: dict[str, set[str]] = defaultdict(set)
    for grant in grants:
        resources[grant.resource.resource_type].add(grant.resource.resource_id)
    rows = active_relationship_model().objects.using(using)
    max_params = connections[rows.db].features.max_query_params
    existing: set[tuple[str, ...]] = set()
    for resource_type, ids in sorted(resources.items()):
        batch_size = max_params - 1 if max_params is not None else len(ids)
        for resource_ids in batched(sorted(ids), batch_size):
            matches = rows.filter(resource_type=resource_type, resource_id__in=resource_ids).wire_values()
            existing.update(tuple(row[field] for field in WIRE_VALUE_FIELDS) for row in matches)
    return existing


def _is_literal_ref(value: str) -> bool:
    """Return whether ``value`` is a slash-form REBAC ref rather than a row xref.

    A literal ref names its type with a ``<ns>/type`` token (``angee/role:admin``);
    a row xref uses the loader's dotted ``<addon>.<xref>`` form (``iam.alice``) and
    resolves through the ledger.
    """

    return "/" in value.split(":", 1)[0]
