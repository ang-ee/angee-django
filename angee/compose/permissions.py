"""Additive REBAC-schema extension seam — domain vocabulary stays in its addon.

``django-zed-rebac`` owns permission sync: it reads one ``permissions.zed`` per
installed app, parses it, and hard-errors on a definition declared twice. The
SpiceDB schema language it accepts has no ``extend`` — a definition is owned,
whole, by exactly one file. That makes the owning file the only place a relation
can be declared, so a consumer addon that needs its own role vocabulary on
another addon's resource would otherwise have to edit that addon's zed. That
leaks domain vocabulary across ownership boundaries — the target addon should
own the *seam*, each extending addon its own vocabulary.

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
from rebac.schema import Definition, Schema, parse_zed, resolve_schema_path, validate_schema
from rebac.schema import render_zed as render_schema

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
        # Repointing is composition output; later passes still merge the
        # authored input captured on this AppConfig before its first repoint.
        path = (
            app_config._angee_rebac_schema_source
            if hasattr(app_config, "_angee_rebac_schema_source")
            else resolve_schema_path(app_config)
        )
        if path is None:
            continue
        package = app_config.name
        schema = _parse(path, package)
        bases[package] = schema
        for definition in schema.definitions:
            previous = owner_of.get(definition.resource_type)
            if previous is not None:
                raise SchemaExtensionError(
                    f"Duplicate definition {definition.resource_type!r} found in {previous} and {package}"
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

    fragment_revision = {package: fragment.headers.get("rebac_schema_revision", "0") for package, fragment in fragments}

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
            merged_def if d.resource_type == resource_type else d for d in owner_schema.definitions
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

    merged = base
    for package, extension in contributed:
        try:
            merged = merged.extend(
                relations=extension.relations,
                permission_arms=extension.permissions,
            )
        except ValueError as error:
            raise SchemaExtensionError(f"{package}: {error}") from error
    return merged


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

    emitted = _clone_schema(schema)
    emitted.headers.setdefault("rebac_package", package)
    emitted.headers.setdefault("rebac_schema_revision", "0")
    return (
        "// Merged REBAC schema — authored base plus additive extensions.\n"
        f"// {GENERATED_SENTINEL}\n" + render_schema(emitted)
    )


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
        if not hasattr(app_config, "_angee_rebac_schema_source"):
            app_config._angee_rebac_schema_source = resolve_schema_path(app_config)
        app_config.rebac_schema = str((runtime_dir / merged_schema_relpath(package)).resolve())
