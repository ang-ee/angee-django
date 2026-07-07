"""Additive REBAC-schema extension seam — domain vocabulary stays in its addon.

``django-zed-rebac`` owns permission sync: it reads one ``permissions.zed`` per
installed app, parses it, and hard-errors on a definition declared twice. The
SpiceDB schema language it accepts has no ``extend`` — a definition is owned,
whole, by exactly one file. That makes the owning file the only place a relation
can be declared, so a consumer addon that needs a company-scoped role binding on
``iam/company`` has historically had to edit the framework's ``iam`` zed. That
leaks domain vocabulary (``accountant``, ``salesperson``, …) into a
framework-owned file — the framework should own the *seam*, each addon its own
vocabulary.

This module is that seam. A consumer addon contributes to a definition owned by
another addon through a sibling **``permissions.extends.zed``** fragment. Each
``definition <target> { … }`` block in the fragment names an existing definition
and lists the relations it contributes and the permission arms it unions in.
``django-zed-rebac`` never reads the fragment (it only reads ``permissions.zed``),
so there is no duplicate-definition collision. Instead the composer merges every
fragment into its target's owning package at build time, emits the merged
effective zed into the runtime tree, and repoints that package's
``AppConfig.rebac_schema`` at it — so ``rebac sync`` / ``rebac check`` /
``reconcile_permissions`` all read the merged superset with no library change.

Merge semantics (deterministic; composition order is ``sorted`` by contributor
package name):

- Contributed **relations** are appended to the target definition. A relation
  name already present on the base — or contributed by two fragments — is a
  hard collision (fail fast).
- Contributed **permission arms** are unioned (``+``) into the base permission
  of the same name, contributors in sorted order. A fragment permission whose
  name the base does not declare is a hard error (there is no arm to extend).
- Extending a definition no installed package declares is a hard error.

The merged definition's identity changes whenever any contribution changes: the
relation/arm lines move the file's bytes (so ``angee build --check`` drifts) and
the per-relation/permission payload hash moves (so ``rebac sync`` re-applies).
The emitted header additionally records each contributor and its fragment
revision in ``@rebac_extended_by`` for provenance. The base package does **not**
bump its own ``@rebac_schema_revision`` for an additive extension — the
contribution is owned, and revisioned, by the contributing addon.

Dormant by construction: with no ``permissions.extends.zed`` anywhere, every
entry point returns empty / no-op and nothing is emitted or repointed.
"""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path

from django.apps import AppConfig
from rebac.schema.ast import (
    AllowedSubject,
    Caveat,
    ConstBinding,
    Definition,
    FieldBinding,
    PermArrow,
    PermBinOp,
    PermExpr,
    Permission,
    PermNil,
    PermRef,
    Relation,
    Schema,
)
from rebac.schema.parser import parse_zed, validate_schema

from angee.fs import GENERATED_SENTINEL

__all__ = [
    "EXTENSION_FILENAME",
    "SchemaExtensionError",
    "extension_source_map",
    "apply_schema_paths",
    "merged_schemas",
    "merged_schema_relpath",
]

# A consumer addon contributes to another addon's definitions through this
# sibling of the library-owned ``permissions.zed``. Its presence is the marker;
# the library never reads it.
EXTENSION_FILENAME = "permissions.extends.zed"
BASE_FILENAME = "permissions.zed"

# Merged effective zed lives under this runtime subtree, one file per owning
# package: ``runtime/permissions/<package>.zed``.
_MERGED_SUBDIR = "permissions"


class SchemaExtensionError(RuntimeError):
    """Raised when a ``permissions.extends.zed`` fragment cannot be merged."""


# ---------- discovery ----------


def _schema_path(app_config: AppConfig, filename: str) -> Path | None:
    """Return an app's schema file path if it exists, matching ``rebac sync``.

    An app with no filesystem ``path`` (a build-time model stand-in) carries no
    zed and is skipped.
    """

    root = getattr(app_config, "path", None)
    if root is None:
        return None
    path = Path(root) / filename
    return path if path.exists() else None


def _parse(path: Path, package: str) -> Schema:
    """Parse one zed file, wrapping any failure with its owning package."""

    try:
        return parse_zed(path.read_text(encoding="utf-8"))
    except Exception as error:  # noqa: BLE001 — re-raised with provenance
        raise SchemaExtensionError(f"{package}: {error}") from error


def _base_index(
    app_configs: Iterable[AppConfig],
) -> tuple[dict[str, Schema], dict[str, str]]:
    """Return ``package -> base Schema`` and ``resource_type -> owning package``.

    The owner index is the one source of truth for which package a definition
    belongs to; a definition declared by two packages is the library's own
    duplicate-definition error, surfaced here so the merge fails the same way.
    """

    bases: dict[str, Schema] = {}
    owner_of: dict[str, str] = {}
    for app_config in app_configs:
        path = _schema_path(app_config, BASE_FILENAME)
        if path is None:
            continue
        package = app_config.name
        schema = _parse(path, package)
        bases[package] = schema
        for definition in schema.definitions:
            previous = owner_of.get(definition.resource_type)
            if previous is not None:
                raise SchemaExtensionError(
                    f"Duplicate definition {definition.resource_type!r} found in "
                    f"{previous} and {package}"
                )
            owner_of[definition.resource_type] = package
    return bases, owner_of


def _extension_fragments(app_configs: Iterable[AppConfig]) -> list[tuple[str, Schema]]:
    """Return ``(contributor package, fragment Schema)`` in composition order."""

    fragments: list[tuple[str, Schema]] = []
    for app_config in app_configs:
        path = _schema_path(app_config, EXTENSION_FILENAME)
        if path is None:
            continue
        fragments.append((app_config.name, _parse(path, app_config.name)))
    fragments.sort(key=lambda item: item[0])
    return fragments


# ---------- merge ----------


def merged_schemas(app_configs: Iterable[AppConfig]) -> dict[str, Schema]:
    """Return ``{owning package -> merged full Schema}`` for extended packages.

    Only packages whose base definitions receive a contribution appear. The
    merged Schema is the package's whole ``permissions.zed`` with each extended
    definition replaced by ``base + contributions``; unextended definitions and
    caveats pass through so the emitted file is a faithful superset the library
    can sync (and prune against) as that package's schema.

    Empty when no ``permissions.extends.zed`` exists — the dormant path.
    """

    app_configs = list(app_configs)
    fragments = _extension_fragments(app_configs)
    if not fragments:
        return {}

    bases, owner_of = _base_index(app_configs)

    # target resource_type -> ordered list of (contributor package, extension def)
    contributions: dict[str, list[tuple[str, Definition]]] = {}
    for package, fragment in fragments:
        for extension in fragment.definitions:
            owner = owner_of.get(extension.resource_type)
            if owner is None:
                raise SchemaExtensionError(
                    f"{package}: permissions.extends.zed extends "
                    f"{extension.resource_type!r}, which no installed addon declares"
                )
            contributions.setdefault(extension.resource_type, []).append((package, extension))

    fragment_revision = {
        package: fragment.headers.get("rebac_schema_revision", "0")
        for package, fragment in fragments
    }

    merged: dict[str, Schema] = {}
    for resource_type, contributed in contributions.items():
        owner = owner_of[resource_type]
        base_schema = bases[owner]
        base_def = base_schema.get_definition(resource_type)
        assert base_def is not None  # owner_of guarantees it
        merged_def = _merge_definition(base_def, contributed)

        owner_schema = merged.get(owner)
        if owner_schema is None:
            owner_schema = _clone_schema(base_schema)
            _record_provenance(owner_schema, contributed, fragment_revision)
            merged[owner] = owner_schema
        else:
            _record_provenance(owner_schema, contributed, fragment_revision)
        owner_schema.definitions = [
            merged_def if d.resource_type == resource_type else d
            for d in owner_schema.definitions
        ]

    for owner, schema in merged.items():
        errors = validate_schema(schema)
        if errors:
            raise SchemaExtensionError(f"{owner}: merged schema invalid: {'; '.join(errors)}")
    return merged


def _merge_definition(
    base: Definition,
    contributed: list[tuple[str, Definition]],
) -> Definition:
    """Merge contributed relations and permission arms into ``base``."""

    relations = list(base.relations)
    seen_relations = {relation.name for relation in base.relations}
    for package, extension in contributed:
        for relation in sorted(extension.relations, key=lambda r: r.name):
            if relation.name in seen_relations:
                raise SchemaExtensionError(
                    f"{package}: relation {base.resource_type}#{relation.name!r} "
                    "already declared — a contributed relation cannot collide with "
                    "the base or another contributor"
                )
            seen_relations.add(relation.name)
            relations.append(relation)

    permissions = {permission.name: permission for permission in base.permissions}
    for package, extension in contributed:
        for arm in sorted(extension.permissions, key=lambda p: p.name):
            current = permissions.get(arm.name)
            if current is None:
                raise SchemaExtensionError(
                    f"{package}: permission {base.resource_type}#{arm.name!r} is not "
                    "declared by the base — an extension unions an arm into an "
                    "existing permission, it cannot introduce one"
                )
            permissions[arm.name] = Permission(
                name=arm.name,
                expression=PermBinOp("+", current.expression, arm.expression),
                raw_text="",
            )

    return Definition(
        resource_type=base.resource_type,
        relations=tuple(sorted(relations, key=lambda r: r.name)),
        permissions=tuple(sorted(permissions.values(), key=lambda p: p.name)),
    )


def _clone_schema(schema: Schema) -> Schema:
    """Return a shallow, independently-mutable copy (frozen AST nodes shared)."""

    return Schema(
        definitions=list(schema.definitions),
        caveats=list(schema.caveats),
        directives=list(schema.directives),
        headers=dict(schema.headers),
    )


def _record_provenance(
    schema: Schema,
    contributed: list[tuple[str, Definition]],
    fragment_revision: dict[str, str],
) -> None:
    """Fold contributors into the merged schema's ``@rebac_extended_by`` header."""

    existing = schema.headers.get("rebac_extended_by", "")
    entries = {item for item in (chunk.strip() for chunk in existing.split(",")) if item}
    for package, _extension in contributed:
        entries.add(f"{package}@{fragment_revision.get(package, '0')}")
    schema.headers["rebac_extended_by"] = ", ".join(sorted(entries))


# ---------- render ----------


def render_zed(package: str, schema: Schema) -> str:
    """Render a merged Schema to deterministic zed text the library round-trips.

    Byte-stable: definitions, relations, permissions, caveats and subject unions
    are sorted; compound permission expressions are fully parenthesised. The
    ``GENERATED_SENTINEL`` marks the file as build output.
    """

    lines = [
        "// Merged REBAC schema — base permissions.zed plus additive extensions.",
        f"// {GENERATED_SENTINEL}",
        f"// @rebac_package: {schema.headers.get('rebac_package', package)}",
        f"// @rebac_schema_revision: {schema.headers.get('rebac_schema_revision', '0')}",
    ]
    extended_by = schema.headers.get("rebac_extended_by")
    if extended_by:
        lines.append(f"// @rebac_extended_by: {extended_by}")
    lines.append("")

    for caveat in sorted(schema.caveats, key=lambda c: c.name):
        lines.append(_render_caveat(caveat))
    for definition in sorted(schema.definitions, key=lambda d: d.resource_type):
        lines.append(_render_definition(definition))
    return "\n".join(lines) + "\n"


def _render_caveat(caveat: Caveat) -> str:
    params = ", ".join(f"{param.name} {param.type}" for param in caveat.params)
    return f"caveat {caveat.name}({params}) {{\n{caveat.expression}\n}}\n"


def _render_definition(definition: Definition) -> str:
    relations = sorted(definition.relations, key=lambda r: r.name)
    permissions = sorted(definition.permissions, key=lambda p: p.name)
    lines = [f"definition {definition.resource_type} {{"]
    for relation in relations:
        lines.append(f"    {_render_relation(relation)}")
    if relations and permissions:
        lines.append("")
    for permission in permissions:
        lines.append(f"    permission {permission.name} = {_render_expr(permission.expression)}")
    lines.append("}")
    return "\n".join(lines) + "\n"


def _render_relation(relation: Relation) -> str:
    subjects = sorted(
        relation.allowed_subjects,
        key=lambda s: (s.type, s.id, s.relation, s.wildcard, s.with_caveat),
    )
    rendered = " | ".join(_render_subject(subject) for subject in subjects)
    suffix = " with expiration" if relation.with_expiration else ""
    line = f"relation {relation.name}: {rendered}{suffix}"
    backing = relation.backing
    if backing is None:
        return line
    if isinstance(backing, ConstBinding):
        return f"{line} // rebac:const={backing.target_id}"
    if isinstance(backing, FieldBinding):
        return f"{line} // rebac:field={backing.attname}"
    raise SchemaExtensionError(
        f"{relation.name}: unsupported relation backing kind {backing.kind!r}"
    )


def _render_subject(subject: AllowedSubject) -> str:
    # Mirrors the library's five subject shapes so the emitted file re-parses
    # to the same AllowedSubject (specific-id forms are the universal-admin
    # pattern; dropping `id` would widen a single-role union).
    if subject.wildcard:
        base = f"{subject.type}:*"
    elif subject.id and subject.relation:
        base = f"{subject.type}:{subject.id}#{subject.relation}"
    elif subject.id:
        base = f"{subject.type}:{subject.id}"
    elif subject.relation:
        base = f"{subject.type}#{subject.relation}"
    else:
        base = subject.type
    if subject.with_caveat:
        base += f" with {subject.with_caveat}"
    return base


def _render_expr(expr: PermExpr) -> str:
    if isinstance(expr, PermNil):
        return "nil"
    if isinstance(expr, PermRef):
        return expr.name
    if isinstance(expr, PermArrow):
        return f"{expr.via}->{expr.target}"
    if isinstance(expr, PermBinOp):
        return f"({_render_expr(expr.left)} {expr.op} {_render_expr(expr.right)})"
    raise SchemaExtensionError(f"unknown expression node: {type(expr).__name__}")


# ---------- build-time wiring ----------


def merged_schema_relpath(package: str) -> Path:
    """Return the merged zed path relative to the runtime dir for ``package``."""

    return Path(_MERGED_SUBDIR) / f"{package}.zed"


def extension_source_map(app_configs: Iterable[AppConfig]) -> dict[Path, str]:
    """Return ``{runtime-relative path -> merged zed text}`` for emission.

    Consumed by :meth:`Runtime.render_sources` so the merged files ride the one
    emit/drift/clean/sentinel lifecycle. Empty when dormant.
    """

    return {
        merged_schema_relpath(package): render_zed(package, schema)
        for package, schema in merged_schemas(app_configs).items()
    }


def apply_schema_paths(app_configs: Iterable[AppConfig], runtime_dir: Path) -> None:
    """Repoint each extended package's ``rebac_schema`` at its merged zed.

    ``rebac sync`` / ``rebac check`` / ``reconcile_permissions`` resolve a
    package's schema as ``Path(app_config.path) / app_config.rebac_schema``; an
    absolute value wins (``Path('/a') / '/b' == Path('/b')``), so pointing at the
    emitted merged file makes every reader see the superset. No-op when dormant.
    """

    app_configs = list(app_configs)
    extended = merged_schemas(app_configs)
    if not extended:
        return
    by_name = {app_config.name: app_config for app_config in app_configs}
    for package in extended:
        app_config = by_name.get(package)
        if app_config is None:
            continue
        app_config.rebac_schema = str((runtime_dir / merged_schema_relpath(package)).resolve())
