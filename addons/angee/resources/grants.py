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

from collections.abc import Iterable, Mapping

from django.db import models
from rebac import (
    ObjectRef,
    RelationshipTuple,
    SubjectRef,
    anonymous_actor,
    backend,
    to_subject_ref,
    write_relationships,
)
from rebac.models import active_relationship_model
from rebac.schema.walker import find_relation, subject_allowed_by_relation

from angee.base.identity import canonical_subject_ref
from angee.base.refs import ancestor_object_refs
from angee.resources.entries import GrantGroup, GrantRow
from angee.resources.exceptions import ResourceLoadError
from angee.resources.widgets import resolve_xref


def materialize_grant_groups(
    groups: Iterable[GrantGroup],
    *,
    ledger_model: type[models.Model],
    addon_aliases: Mapping[str, str],
) -> tuple[int, int]:
    """Write every group's grant tuples idempotently; return ``(created, skipped)``.

    Rows are deduplicated by their natural tuple key before writing, so a repeat
    within one load — or a re-load of an unchanged fixture — is a no-op.
    """

    resolved: dict[tuple[str, ...], RelationshipTuple] = {}
    for group in groups:
        for row in group.rows:
            for grant in _grant_tuples(row, ledger_model, addon_aliases):
                resolved.setdefault(grant.canonical_key(), grant)
    if not resolved:
        return 0, 0

    relationship_model = active_relationship_model()
    new_grants: list[RelationshipTuple] = []
    skipped = 0
    for grant in resolved.values():
        if _grant_exists(relationship_model, grant):
            skipped += 1
        else:
            new_grants.append(grant)
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
    ledger_model: type[models.Model],
    addon_aliases: Mapping[str, str],
) -> list[RelationshipTuple]:
    """Return the explicit grant and compatible grants on its MTI ancestors.

    Literal refs and plain rows yield exactly one tuple. Ancestor expansion
    follows the native schema's allowed subjects and relation backing. Never
    filter the explicit tuple: invalid requests must fail at the native writer.
    """

    subject = _resolve_subject(row, ledger_model, addon_aliases)
    own, *ancestors = _resolve_resource_refs(row, ledger_model, addon_aliases)
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
    ledger_model: type[models.Model],
    addon_aliases: Mapping[str, str],
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
    resolved = _resolve_row(row, value, ledger_model, addon_aliases)
    return list(ancestor_object_refs(resolved))


def _resolve_subject(
    row: GrantRow,
    ledger_model: type[models.Model],
    addon_aliases: Mapping[str, str],
) -> SubjectRef:
    """Resolve the grant subject to a subject reference."""

    value = row.subject
    if value == "*":
        return anonymous_actor()
    if _is_literal_ref(value):
        return canonical_subject_ref(value)
    return to_subject_ref(_resolve_row(row, value, ledger_model, addon_aliases))


def _resolve_row(
    row: GrantRow,
    value: str,
    ledger_model: type[models.Model],
    addon_aliases: Mapping[str, str],
) -> models.Model:
    """Resolve one row xref through the ledger, or raise with the grant location."""

    try:
        return resolve_xref(value, ledger_model, addon_aliases)
    except ValueError as error:
        raise ResourceLoadError(f"{row.entry.display} grant {row.index}: {error}") from error


def _grant_exists(relationship_model: type[models.Model], grant: RelationshipTuple) -> bool:
    """Return whether a tuple with ``grant``'s natural key already exists."""

    return relationship_model._default_manager.filter(
        resource_type=grant.resource.resource_type,
        resource_id=grant.resource.resource_id,
        relation=grant.relation,
        subject_type=grant.subject.subject_type,
        subject_id=grant.subject.subject_id,
        optional_subject_relation=grant.subject.optional_relation,
        caveat_name=grant.caveat_name,
    ).exists()


def _is_literal_ref(value: str) -> bool:
    """Return whether ``value`` is a slash-form REBAC ref rather than a row xref.

    A literal ref names its type with a ``<ns>/type`` token (``angee/role:admin``);
    a row xref uses the loader's dotted ``<addon>.<xref>`` form (``iam.alice``) and
    resolves through the ledger.
    """

    return "/" in value.split(":", 1)[0]
