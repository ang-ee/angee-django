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
``AppConfig.rebac_schema`` at it — so native ``rebac sync --prune-stale``
and ``rebac check`` read the same merged superset.

Merge semantics (deterministic; composition order is ``sorted`` by contributor
package name):

- Contributed **relations** are appended to the target definition. A relation
  name already present on the base — or contributed by two fragments — is a
  hard collision (fail fast).
- Contributed **permission arms** are unioned (``+``) into the base permission
  of the same name, contributors in sorted order. A fragment may introduce a
  new capability permission; exactly one contributing package owns that name.
  New field gates additionally require a concrete column contributed by that
  same package's donor, using canonical Django field names rather than attnames
  or many-to-many fields. A second package cannot extend a contributed
  declaration; base-declared permissions keep the ordinary union-arm behavior.
  Missing field ownership and all declaration-name collisions fail closed.
- Extending a definition no installed package declares is a hard error.

The merged definition's identity changes whenever any contribution changes: the
relation/arm lines move the file's bytes (so ``angee check`` reports drift) and
the per-relation/permission payload hash moves (so ``rebac sync`` re-applies).
The emitted header additionally records each contributor and its fragment
revision in ``@rebac_extended_by`` for provenance. The base package does **not**
bump its own ``@rebac_schema_revision`` for an additive extension — the
contribution is owned, and revisioned, by the contributing addon.

With no extension fragments, merging emits nothing. Phase-2 binding still
connects manifest-declared permission files to the upstream AppConfig seam.
The runtime's ``angee.E024`` check reads those effective files and rejects
caveated relation subjects because framework actor-scoped querysets have no
request caveat context, even though the local backend supports point checks.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import replace
from pathlib import Path
from typing import TYPE_CHECKING

from django.apps import AppConfig
from django.core.exceptions import ImproperlyConfigured
from django.db import models
from rebac.resources import model_resource_type
from rebac.schema import Definition, Permission, Schema, parse_zed, resolve_schema_path, validate_schema
from rebac.schema import render_zed as render_schema

from angee.addons import addon_facts, is_angee_addon
from angee.base.permissions import declared_grants
from angee.compose.declarations import BuildIntegration, DeclarationValue, ModelDeclaration
from angee.compose.runtime import GENERATED_SENTINEL

if TYPE_CHECKING:
    from angee.compose.model_composition import ModelComposition
    from angee.compose.runtime import Runtime

__all__ = [
    "EXTENSION_FILENAME",
    "PermissionIntegration",
    "composed_grants",
    "field_gate_owners",
    "SchemaExtensionError",
    "extension_source_map",
    "schema_path_wiring",
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


def declared_schema_path(app_config: AppConfig) -> Path | None:
    """Resolve authored input independently of previously bound effective output."""

    if not is_angee_addon(app_config):
        # Native Django apps have no addon entry point. Capture their upstream
        # declaration before a derived merged path is assigned to the same seam.
        if (
            "_angee_rebac_schema_effective" not in app_config.__dict__
            or getattr(app_config, "rebac_schema", None) != getattr(app_config, "_angee_rebac_schema_effective")
        ):
            setattr(app_config, "_angee_rebac_schema_source", resolve_schema_path(app_config))
        return getattr(app_config, "_angee_rebac_schema_source")

    facts = addon_facts(app_config.name).get("base", {})
    if not isinstance(facts, Mapping):
        raise ImproperlyConfigured(f"{app_config.name}: tool.angee.base must be a table")
    permissions = facts.get("permissions", "permissions.zed")
    if not isinstance(permissions, str) or not permissions:
        raise ImproperlyConfigured(f"{app_config.name}: tool.angee.base.permissions must be a non-empty path")
    path = Path(app_config.path) / permissions
    configured = getattr(app_config, "rebac_schema", None)
    bound = app_config.__dict__.get("_angee_rebac_schema_effective")
    if configured is not None and configured != bound:
        configured_path = Path(app_config.path) / configured
        if configured_path.resolve() != path.resolve():
            raise ImproperlyConfigured(
                f"{app_config.name}: declare permissions in pyproject.toml [tool.angee.base]; "
                "AppConfig.rebac_schema conflicts with the package declaration"
            )
    if facts.get("permissions") is not None and not path.is_file():
        raise ImproperlyConfigured(f"{app_config.name}: declared permissions file does not exist: {path}")
    return path if path.is_file() else None


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
        path = declared_schema_path(app_config)
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


def merged_schemas(
    app_configs: Iterable[AppConfig],
    *,
    field_owners: Mapping[str, Mapping[str, str]] | None = None,
) -> dict[str, Schema]:
    """Return ``{owning package -> merged full Schema}`` for extended packages.

    Only packages whose base definitions receive a contribution appear. The
    merged Schema is the package's whole ``permissions.zed`` with each extended
    definition replaced by ``base + contributions``; unextended definitions and
    caveats pass through so the emitted file is a faithful superset the library
    can sync (and prune against) as that package's schema.

    Empty when no ``permissions.extends.zed`` exists — the dormant path.
    """

    app_configs = list(app_configs)
    for app_config in app_configs:
        declared_schema_path(app_config)
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
        merged_def = _merge_definition(base_def, contributed, field_owners=field_owners)

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
    *,
    field_owners: Mapping[str, Mapping[str, str]] | None,
) -> Definition:
    """Extend base arms natively and append each addon's uniquely owned declarations."""

    merged = base
    base_permissions = {permission.name for permission in base.permissions}
    owners = (field_owners or {}).get(base.resource_type, {})
    declarations: dict[str, Permission] = {}
    for package, extension in contributed:
        arms: list[Permission] = []
        for permission in extension.permissions:
            verb, separator, field = permission.name.partition("__")
            if permission.name in base_permissions:
                arms.append(permission)
                continue
            owner = owners.get(field)
            if separator and verb in {"read", "write"} and owner != package:
                canonical = field.removesuffix("_id")
                detail = (
                    "no field ownership map supplied" if field_owners is None
                    else f"use the canonical field name {canonical!r}, owned by {owners[canonical]!r}"
                    if field.endswith("_id") and canonical in owners
                    else f"donor owner is {owner!r}" if owner is not None
                    else "no concrete donor column is declared (base, many-to-many, or unknown field)"
                )
                raise SchemaExtensionError(
                    f"{package}: {base.resource_type} field gate {permission.name!r} on {field!r} "
                    f"must belong to this package; {detail}"
                )
            if permission.name in declarations:
                raise SchemaExtensionError(
                    f"{package}: permission {base.resource_type}#{permission.name} already declared by a contributor"
                )
            declarations[permission.name] = permission
        try:
            merged = merged.extend(
                relations=extension.relations,
                permission_arms=arms,
            )
        except ValueError as error:
            raise SchemaExtensionError(f"{package}: {error}") from error
    collisions = declarations.keys() & {relation.name for relation in merged.relations}
    if collisions:
        raise SchemaExtensionError(
            f"{base.resource_type}: permission declarations collide with relations: {sorted(collisions)}"
        )
    if not declarations:
        return merged
    return replace(merged, permissions=tuple(sorted(
        (*merged.permissions, *declarations.values()), key=lambda permission: permission.name,
    )))


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


def extension_source_map(
    app_configs: Iterable[AppConfig],
    *,
    field_owners: Mapping[str, Mapping[str, str]] | None = None,
) -> dict[Path, str]:
    """Return ``{runtime-relative path -> merged zed text}`` for emission.

    Consumed by :meth:`Runtime.render_sources` so the merged files ride the one
    emit/drift/clean/sentinel lifecycle. Empty when dormant.
    """

    return {
        merged_schema_relpath(package): render_zed(package, schema)
        for package, schema in merged_schemas(app_configs, field_owners=field_owners).items()
    }


PERMISSION_WIRING = Path("_permission_paths.py")


def schema_path_wiring(
    app_configs: Iterable[AppConfig], *, sources: Mapping[Path, str],
) -> str:
    """Render native REBAC path assignments; serving does not import this compiler."""

    lines = [
        '"""Generated native REBAC schema paths."""',
        "from pathlib import Path",
        "from django.apps import apps",
        "from django.core.exceptions import ImproperlyConfigured",
        "from rebac.schema import resolve_schema_path",
        "",
    ]
    for app_config in app_configs:
        source = declared_schema_path(app_config)
        merged = merged_schema_relpath(app_config.name)
        if (
            merged not in sources
            and "_angee_rebac_schema_effective" not in app_config.__dict__
            and (not is_angee_addon(app_config) or source is None)
        ):
            continue
        lines.append(f"app = apps.get_app_config({app_config.label!r})")
        if source is None:
            source_expression = "None"
        elif source.is_relative_to(Path(app_config.path)):
            relative = source.relative_to(Path(app_config.path))
            source_expression = f"Path(app.path) / {str(relative)!r}"
        elif is_angee_addon(app_config):
            # An explicit package path remains the source of truth even when
            # absolute; native AppConfig resolution does not read package metadata.
            source_expression = f"Path({str(source)!r})"
        else:
            # Native external declarations remain late-bound to their AppConfig;
            # an already-bound config retains its original source on reimport.
            source_expression = (
                "getattr(app, '_angee_rebac_schema_source', None) "
                "if getattr(app, 'rebac_schema', None) == "
                "getattr(app, '_angee_rebac_schema_effective', object()) "
                "else resolve_schema_path(app)"
            )
        lines.append(f"app._angee_rebac_schema_source = {source_expression}")
        if source is not None and (is_angee_addon(app_config) or source.is_relative_to(Path(app_config.path))):
            lines.extend((
                "if not app._angee_rebac_schema_source.is_file():",
                "    raise ImproperlyConfigured("
                f"{f'Missing packaged permission source for {app_config.name}; rebuild explicitly'!r})",
            ))
        if merged in sources:
            lines.extend((
                f"effective = Path(__file__).resolve().parent / {str(merged)!r}",
                "if not effective.is_file():",
                "    raise ImproperlyConfigured("
                f"{f'Missing prebuilt permission schema for {app_config.name}; rebuild explicitly'!r})",
                "app.rebac_schema = str(effective)",
            ))
        else:
            lines.append(
                "app.rebac_schema = str(app._angee_rebac_schema_source.resolve()) "
                "if app._angee_rebac_schema_source is not None else None"
            )
        lines.extend(("app._angee_rebac_schema_effective = app.rebac_schema", ""))
    return "\n".join(lines) + "\n"


class PermissionIntegration(BuildIntegration):
    """Project Angee's REBAC contracts without making the compiler own them."""

    def model_declaration(self, composition: ModelComposition, source: type[models.Model]) -> ModelDeclaration:
        """Preserve per-model Meta declarations and additive same-record grants."""

        # The native owner also defines Django model mixins. Model projection
        # runs in phase 2; schema-source/test-fixture imports may run before setup.
        from rebac.mixins import REBAC_META_OPTIONS

        meta_options = {
            option: value for option in REBAC_META_OPTIONS
            if (value := getattr(source._meta, option, None)) is not None
        }
        grants = composed_grants(composition, source)
        attributes: dict[str, DeclarationValue] = {}
        if grants or "rebac_grantable" in source.__dict__ or composition.parent(source) is not None:
            # A concrete parent must never donate authority to its MTI child.
            attributes["rebac_grantable"] = dict(grants)
        return ModelDeclaration(attributes=attributes, meta_options=meta_options)

    def render_sources(self, runtime: Runtime) -> Mapping[Path, str]:
        """Render effective permission files using the compiler's donor facts."""

        sources = extension_source_map(runtime.addons, field_owners=field_gate_owners(runtime.composition))
        sources[PERMISSION_WIRING] = schema_path_wiring(runtime.addons, sources=sources)
        return sources

    def runtime_imports(self, runtime: Runtime) -> tuple[str, ...]:
        """Import the same native path assignments during build and serving."""

        return (f"{runtime.runtime_module}.{PERMISSION_WIRING.stem}",)


def composed_grants(composition: ModelComposition, source: type[models.Model]) -> dict[str, str]:
    """Merge explicit same-record grants without inheriting parent authority."""

    grants: dict[str, str] = {}
    for owner in (source, *composition.donors(source)):
        for relation, permission in declared_grants(owner).items():
            previous = grants.setdefault(relation, permission)
            if previous != permission:
                raise ImproperlyConfigured(
                    f"{source._meta.label_lower} composes conflicting rebac_grantable "
                    f"permissions for {relation!r}: {previous!r} and {permission!r}."
                )
    return grants


def field_gate_owners(composition: ModelComposition) -> dict[str, dict[str, str]]:
    """Attribute concrete donor columns to their permission-schema owners."""

    owners: dict[str, dict[str, str]] = {}
    for source in composition.ordered_models:
        resource_type = model_resource_type(source)
        if not resource_type:
            continue
        columns = {field.name for field in composition.contributed_fields(source) if field.concrete}
        owners[resource_type] = {
            name: package for name, package in sorted(composition.contributed_field_origins(source))
            if name in columns
        }
    return dict(sorted(owners.items()))
