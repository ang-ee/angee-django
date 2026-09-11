"""Database guards compiled from static external-ownership declarations."""

# ruff: noqa: E501

from __future__ import annotations

import hashlib
from typing import Any

from django.apps import apps as django_apps
from django.core.exceptions import ImproperlyConfigured
from django.db import models

from angee.base.importing import (
    ExternalOwnershipDeclaration,
    ImportCompany,
    ImportSource,
    _declaration,
    current_import_operation,
)


def _guard_name(table: str, guard_key: str = "") -> str:
    digest = hashlib.sha256(f"{table}:{guard_key}".encode()).hexdigest()[:12]
    return f"angee_external_owner_{digest}"


def _sqlite_allows(
    source_type: object,
    source_id: object,
    company_type: object,
    company_id: object,
    model: object,
    pk: object,
    operations: object,
) -> int:
    """SQLite SQL callback mirroring the typed in-process authority."""

    operation = current_import_operation()
    if operation is None:
        return 0
    source = ImportSource(str(source_type or ""), str(source_id or ""))
    company = None
    if company_type or company_id:
        company = ImportCompany(str(company_type or ""), str(company_id or ""))
    return int(
        operation.source == source
        and operation.company == company
        and operation.operation in str(operations).split("\x1f")
        and any(target.permits(str(model), pk) for target in operation.targets)
    )


def register_sqlite_import_scope(connection: Any) -> None:
    """Install the connection-local function used by persistent SQLite triggers."""

    if connection.vendor != "sqlite":
        return
    connection.ensure_connection()
    connection.connection.create_function("angee_import_scope_allows", 7, _sqlite_allows)


def install_external_ownership_guard(
    schema_editor: Any,
    model: type[models.Model],
    declaration: ExternalOwnershipDeclaration | None = None,
    *,
    guard_key: str = "",
) -> None:
    """Install one target table's PostgreSQL or SQLite provenance trigger.

    Consumer-owned runtime migrations call this with their historical target
    model.  The trigger protects raw SQL and cascades in addition to the ORM
    mixin; its shape is deterministic from the model's static declaration.
    """

    declaration = declaration or _declaration(model)
    vendor = schema_editor.connection.vendor
    if vendor not in {"postgresql", "sqlite"}:
        raise NotImplementedError(f"External ownership guards do not support database vendor {vendor!r}.")
    if vendor == "sqlite":
        register_sqlite_import_scope(schema_editor.connection)
        _install_sqlite(schema_editor, model, declaration, guard_key=guard_key)
    else:
        _install_postgresql(schema_editor, model, declaration, guard_key=guard_key)


def install_import_scope_function(schema_editor: Any) -> None:
    """Install the stable PostgreSQL authority predicate for owner triggers."""

    if schema_editor.connection.vendor != "postgresql":
        return
    schema_editor.execute(
        """
        CREATE OR REPLACE FUNCTION angee_import_scope_allows(
          source_type text, source_id text, company_type text, company_id text,
          target_model text, target_pk text, allowed_operations text
        ) RETURNS boolean AS $$
        DECLARE raw_scope text := NULLIF(current_setting('angee.import_scope', true), '');
        DECLARE scope jsonb;
        BEGIN
          IF raw_scope IS NULL THEN RETURN FALSE; END IF;
          BEGIN
            scope := raw_scope::jsonb;
          EXCEPTION WHEN invalid_text_representation THEN
            RETURN FALSE;
          END;
          RETURN COALESCE(
            NULLIF(scope->>'run', '') IS NOT NULL
            AND scope->>'source_type' = source_type
            AND scope->>'source_id' = source_id
            AND (scope->>'company_type') IS NOT DISTINCT FROM company_type
            AND (scope->>'company_id') IS NOT DISTINCT FROM company_id
            AND scope->>'operation' = ANY(string_to_array(allowed_operations, chr(31)))
            AND EXISTS (
              SELECT 1 FROM jsonb_array_elements(COALESCE(scope->'targets', '[]'::jsonb)) item
              WHERE item->>'model' = target_model
                AND ((item->>'object_id') IS NULL OR item->>'object_id' = target_pk)
            ),
            FALSE
          );
        END; $$ LANGUAGE plpgsql STABLE;
        """
    )


def remove_external_ownership_guard(schema_editor: Any, model: type[models.Model], *, guard_key: str = "") -> None:
    """Remove the deterministic target table guard."""

    q = schema_editor.quote_name
    name = _guard_name(model._meta.db_table, guard_key)
    table = q(model._meta.db_table)
    if schema_editor.connection.vendor == "postgresql":
        schema_editor.execute(f"DROP TRIGGER IF EXISTS {q(name)} ON {table}")
        schema_editor.execute(f"DROP FUNCTION IF EXISTS {q(name + '_fn')}()")
    elif schema_editor.connection.vendor == "sqlite":
        for suffix in ("insert", "update", "delete"):
            schema_editor.execute(f"DROP TRIGGER IF EXISTS {q(name + '_' + suffix)}")


def install_external_ownership_relation_guard(
    schema_editor: Any,
    owner_model: type[models.Model],
    relation_name: str,
    declaration: ExternalOwnershipDeclaration | None = None,
) -> None:
    """Guard one declared many-to-many through table at the database boundary."""

    relation = owner_model._meta.get_field(relation_name)
    if not relation.many_to_many:
        raise ValueError(f"{owner_model._meta.label}.{relation_name} is not many-to-many.")
    declaration = declaration or _declaration(owner_model)
    if relation_name not in declaration.source_owned_fields:
        raise ValueError(f"{owner_model._meta.label}.{relation_name} is not declared source-owned.")
    through = relation.remote_field.through
    owner_relation = relation.m2m_field_name()
    predicate_new = _endpoint_predicate(
        schema_editor, through, declaration, owner_relation, "NEW", 1, related_declaration=declaration
    )
    predicate_old = _endpoint_predicate(
        schema_editor, through, declaration, owner_relation, "OLD", 1, related_declaration=declaration
    )
    q = schema_editor.quote_name
    table = q(through._meta.db_table)
    name = _guard_name(f"{through._meta.db_table}:{owner_model._meta.label_lower}:{relation_name}")
    if schema_editor.connection.vendor == "postgresql":
        install_import_scope_function(schema_editor)
        function = q(name + "_fn")
        schema_editor.execute(
            f"""
            CREATE OR REPLACE FUNCTION {function}() RETURNS trigger AS $$
            BEGIN
              IF TG_OP <> 'DELETE' AND ({predicate_new}) THEN
                RAISE EXCEPTION 'source-owned relation mutation requires matching import authority';
              END IF;
              IF TG_OP <> 'INSERT' AND ({predicate_old}) THEN
                RAISE EXCEPTION 'source-owned relation mutation requires matching import authority';
              END IF;
              RETURN CASE WHEN TG_OP = 'DELETE' THEN OLD ELSE NEW END;
            END; $$ LANGUAGE plpgsql;
            """
        )
        schema_editor.execute(f"DROP TRIGGER IF EXISTS {q(name)} ON {table}")
        schema_editor.execute(
            f"CREATE TRIGGER {q(name)} BEFORE INSERT OR UPDATE OR DELETE ON {table} "
            f"FOR EACH ROW EXECUTE FUNCTION {function}()"
        )
        return
    if schema_editor.connection.vendor != "sqlite":
        raise NotImplementedError(
            f"External ownership guards do not support database vendor {schema_editor.connection.vendor!r}."
        )
    register_sqlite_import_scope(schema_editor.connection)
    conditions = {"insert": predicate_new, "update": f"({predicate_new}) OR ({predicate_old})", "delete": predicate_old}
    for action, condition in conditions.items():
        trigger = q(name + "_" + action)
        schema_editor.execute(f"DROP TRIGGER IF EXISTS {trigger}")
        schema_editor.execute(
            f"CREATE TRIGGER {trigger} BEFORE {action.upper()} ON {table} FOR EACH ROW WHEN {condition} "
            "BEGIN SELECT RAISE(ABORT, 'source-owned relation mutation requires matching import authority'); END"
        )


def remove_external_ownership_relation_guard(
    schema_editor: Any,
    owner_model: type[models.Model],
    relation_name: str,
) -> None:
    """Remove one deterministic many-to-many through-table guard."""

    relation = owner_model._meta.get_field(relation_name)
    through = relation.remote_field.through
    q = schema_editor.quote_name
    name = _guard_name(f"{through._meta.db_table}:{owner_model._meta.label_lower}:{relation_name}")
    if schema_editor.connection.vendor == "postgresql":
        schema_editor.execute(f"DROP TRIGGER IF EXISTS {q(name)} ON {q(through._meta.db_table)}")
        schema_editor.execute(f"DROP FUNCTION IF EXISTS {q(name + '_fn')}()")
    elif schema_editor.connection.vendor == "sqlite":
        for suffix in ("insert", "update", "delete"):
            schema_editor.execute(f"DROP TRIGGER IF EXISTS {q(name + '_' + suffix)}")


def _column(model: type[models.Model], field_name: str) -> str:
    return model._meta.get_field(field_name).column


def _row_value(schema_editor: Any, model: type[models.Model], field_name: str, row: str, depth: int = 0) -> str:
    """Return SQL reading an inherited field from one physical-table row."""

    q = schema_editor.quote_name
    field = model._meta.get_field(field_name)
    if field.model._meta.db_table == model._meta.db_table:
        return f"{row}.{q(field.column)}"
    for parent, link in model._meta.parents.items():
        try:
            parent_field = parent._meta.get_field(field_name)
        except LookupError:
            continue
        if parent_field is not field:
            continue
        alias = f"owner_parent_{depth}"
        parent_value = _row_value(schema_editor, parent, field_name, alias, depth + 1)
        return (
            f"(SELECT {parent_value} FROM {q(parent._meta.db_table)} {alias} "
            f"WHERE {alias}.{q(parent._meta.pk.column)} = {row}.{q(link.column)})"
        )
    raise ValueError(f"Cannot resolve {model._meta.label}.{field_name} to an owning physical table.")


def _company_parts(model: type[models.Model], declaration: ExternalOwnershipDeclaration) -> tuple[str, str]:
    if declaration.company_field is None:
        return "", ""
    owner = model
    field = None
    for part in declaration.company_field.split("__"):
        field = owner._meta.get_field(part)
        owner = field.related_model if field.is_relation else owner
    assert field is not None
    company_type = owner._meta.label_lower if field.is_relation else field.model._meta.label_lower
    return company_type, declaration.company_field


def _row_path_value(
    schema_editor: Any,
    model: type[models.Model],
    path: str,
    row: str,
    depth: int = 0,
) -> str:
    """Return SQL following a static foreign-key path from a physical row."""

    head, *tail = path.split("__", 1)
    field = model._meta.get_field(head)
    direct = _row_value(schema_editor, model, head, row, depth)
    if not tail:
        return direct
    if not field.is_relation or field.many_to_many:
        raise ValueError(f"{model._meta.label}.{head} cannot continue company path {path!r}.")
    related = field.related_model
    alias = f"owner_scope_{depth}"
    value = _row_path_value(schema_editor, related, tail[0], alias, depth + 1)
    q = schema_editor.quote_name
    return (
        f"(SELECT {value} FROM {q(related._meta.db_table)} {alias} "
        f"WHERE {alias}.{q(related._meta.pk.column)} = {direct})"
    )


def _related_value(
    schema_editor: Any,
    model: type[models.Model],
    relation_name: str,
    related_field: str,
    row: str,
    index: int,
) -> str:
    """Return a scalar subquery reading one protected endpoint field."""

    q = schema_editor.quote_name
    relation = model._meta.get_field(relation_name)
    related = relation.related_model
    alias = f"owner_endpoint_{index}"
    value = _row_path_value(schema_editor, related, related_field, alias, index + 1)
    return (
        f"(SELECT {value} FROM {q(related._meta.db_table)} {alias} "
        f"WHERE {alias}.{q(related._meta.pk.column)} = {row}.{q(relation.column)})"
    )


def _endpoint_predicate(
    schema_editor: Any,
    model: type[models.Model],
    declaration: ExternalOwnershipDeclaration,
    relation_name: str,
    row: str,
    index: int,
    related_declaration: ExternalOwnershipDeclaration | None = None,
) -> str:
    """Return SQL true when a protected endpoint lacks matching authority."""

    relation = model._meta.get_field(relation_name)
    related = relation.related_model
    if related_declaration is None:
        try:
            related_declaration = _declaration(related)
        except ImproperlyConfigured:  # Historical migration models intentionally lack source class attributes.
            current_related = django_apps.get_model(related._meta.label_lower)
            try:
                related_declaration = _declaration(current_related)
            except ImproperlyConfigured:
                # A protected relation may also point at a shared/native row. It
                # has no external provenance to defend, so this endpoint cannot
                # contribute an ownership violation.
                return "FALSE"
    source_type = _related_value(schema_editor, model, relation_name, "external_source_type", row, index)
    source_id = _related_value(schema_editor, model, relation_name, "external_source_id", row, index)
    company_type, company_column = _company_parts(related, related_declaration)
    company_value = (
        "NULL"
        if not company_column
        else _related_value(schema_editor, model, relation_name, related_declaration.company_field, row, index)
    )
    company_literal = (
        "CAST(NULL AS TEXT)"
        if not company_type
        else "(CASE WHEN "
        + company_value
        + " IS NULL THEN CAST(NULL AS TEXT) ELSE CAST('"
        + company_type.replace("'", "''")
        + "' AS TEXT) END)"
    )
    operations = "\x1f".join(sorted(related_declaration.operations)).replace("'", "''")
    target = related._meta.label_lower.replace("'", "''")
    endpoint_pk = f"{row}.{schema_editor.quote_name(relation.column)}"
    target_pk = (
        f"CAST({endpoint_pk} AS TEXT)" if schema_editor.connection.vendor == "sqlite" else f"{endpoint_pk}::text"
    )
    del declaration
    return (
        f"(COALESCE({source_type}, '') <> '' AND NOT angee_import_scope_allows("
        f"CAST({source_type} AS TEXT), CAST({source_id} AS TEXT), {company_literal}, "
        f"CAST({company_value} AS TEXT), CAST('{target}' AS TEXT), {target_pk}, "
        f"CAST('{operations}' AS TEXT)))"
    )


def _install_postgresql(
    schema_editor: Any,
    model: type[models.Model],
    declaration: ExternalOwnershipDeclaration,
    *,
    guard_key: str = "",
) -> None:
    q = schema_editor.quote_name
    table = q(model._meta.db_table)
    name = _guard_name(model._meta.db_table, guard_key)
    function = q(name + "_fn")
    new_source_type = _row_value(schema_editor, model, "external_source_type", "NEW")
    new_source_id = _row_value(schema_editor, model, "external_source_id", "NEW")
    new_source_key = _row_value(schema_editor, model, "external_source_key", "NEW")
    old_source_type = _row_value(schema_editor, model, "external_source_type", "OLD")
    old_source_id = _row_value(schema_editor, model, "external_source_id", "OLD")
    old_source_key = _row_value(schema_editor, model, "external_source_key", "OLD")
    new_pk = _row_value(schema_editor, model, model._meta.pk.name, "NEW")
    old_pk = _row_value(schema_editor, model, model._meta.pk.name, "OLD")
    company_type, company_column = _company_parts(model, declaration)
    company_expr = (
        "NULL::text"
        if not company_column
        else (
            "(CASE WHEN TG_OP = 'DELETE' THEN "
            f"{_row_path_value(schema_editor, model, declaration.company_field, 'OLD')} "
            f"ELSE {_row_path_value(schema_editor, model, declaration.company_field, 'NEW')} END)::text"
        )
    )
    old_company_expr = (
        "NULL::text"
        if not company_column
        else f"({_row_path_value(schema_editor, model, declaration.company_field, 'OLD')})::text"
    )
    new_company_expr = (
        "NULL::text"
        if not company_column
        else f"({_row_path_value(schema_editor, model, declaration.company_field, 'NEW')})::text"
    )
    changed = (
        " OR ".join(
            f"NEW.{q(_column(model, field))} IS DISTINCT FROM OLD.{q(_column(model, field))}"
            for field in sorted(declaration.source_owned_fields)
            if not model._meta.get_field(field).many_to_many
            and model._meta.get_field(field).model._meta.db_table == model._meta.db_table
        )
        or "FALSE"
    )
    operations = "\x1f".join(sorted(declaration.operations)).replace("'", "''")
    target = model._meta.label_lower.replace("'", "''")
    company_literal = (
        "NULL::text"
        if not company_type
        else f"(CASE WHEN {company_expr} IS NULL THEN NULL::text ELSE '"
        + company_type.replace("'", "''")
        + "'::text END)"
    )
    install_import_scope_function(schema_editor)
    endpoint_new = (
        " OR ".join(
            _endpoint_predicate(schema_editor, model, declaration, relation, "NEW", index)
            for index, relation in enumerate(sorted(declaration.protected_relations), start=1)
        )
        or "FALSE"
    )
    endpoint_old = (
        " OR ".join(
            _endpoint_predicate(schema_editor, model, declaration, relation, "OLD", index)
            for index, relation in enumerate(sorted(declaration.protected_relations), start=1)
        )
        or "FALSE"
    )
    endpoint_update_new = (
        " OR ".join(
            f"(NEW.{q(model._meta.get_field(relation).column)} IS DISTINCT FROM "
            f"OLD.{q(model._meta.get_field(relation).column)} AND "
            f"{_endpoint_predicate(schema_editor, model, declaration, relation, 'NEW', index)})"
            for index, relation in enumerate(sorted(declaration.protected_relations), start=1)
        )
        or "FALSE"
    )
    endpoint_update_old = (
        " OR ".join(
            f"(NEW.{q(model._meta.get_field(relation).column)} IS DISTINCT FROM "
            f"OLD.{q(model._meta.get_field(relation).column)} AND "
            f"{_endpoint_predicate(schema_editor, model, declaration, relation, 'OLD', index)})"
            for index, relation in enumerate(sorted(declaration.protected_relations), start=1)
        )
        or "FALSE"
    )
    # noqa below is unnecessary: the generated SQL stays readable as complete statements.
    sql = f"""
        CREATE OR REPLACE FUNCTION {function}() RETURNS trigger AS $$
        DECLARE owned boolean;
        DECLARE allowed boolean;
        BEGIN
          owned := COALESCE(CASE WHEN TG_OP = 'DELETE' THEN {old_source_type} ELSE {new_source_type} END, '') <> '';
          IF TG_OP <> 'DELETE' AND (({new_source_type} = '') <> ({new_source_id} = '') OR ({new_source_type} = '') <> ({new_source_key} = '')) THEN
            RAISE EXCEPTION 'external provenance must be complete';
          END IF;
          IF TG_OP = 'UPDATE' AND ({new_source_type}, {new_source_id}, {new_source_key}) IS DISTINCT FROM ({old_source_type}, {old_source_id}, {old_source_key}) THEN
            allowed := COALESCE({old_source_type}, '') = ''
              AND COALESCE({old_source_id}, '') = ''
              AND COALESCE({old_source_key}, '') = ''
              AND angee_import_scope_allows(
                {new_source_type}, {new_source_id}, {company_literal}, {company_expr},
                '{target}', {new_pk}::text, '{operations}'
              );
            IF allowed IS DISTINCT FROM TRUE THEN RAISE EXCEPTION 'external provenance is immutable'; END IF;
          END IF;
          IF TG_OP = 'UPDATE' AND COALESCE({old_source_type}, '') <> ''
             AND {new_company_expr} IS DISTINCT FROM {old_company_expr} THEN
            RAISE EXCEPTION 'external ownership company is immutable';
          END IF;
          IF owned AND (TG_OP IN ('INSERT', 'DELETE') OR ({changed})) THEN
            allowed := angee_import_scope_allows(
              CASE WHEN TG_OP = 'DELETE' THEN {old_source_type} ELSE {new_source_type} END,
              CASE WHEN TG_OP = 'DELETE' THEN {old_source_id} ELSE {new_source_id} END,
              {company_literal}, {company_expr}, '{target}',
              (CASE WHEN TG_OP = 'DELETE' THEN {old_pk} ELSE {new_pk} END)::text,
              '{operations}'
            );
            IF allowed IS DISTINCT FROM TRUE THEN
              RAISE EXCEPTION 'source-owned mutation requires matching import authority';
            END IF;
          END IF;
          IF (TG_OP = 'INSERT' AND ({endpoint_new}))
             OR (TG_OP = 'UPDATE' AND ({endpoint_update_new})) THEN
            RAISE EXCEPTION 'source-owned endpoint mutation requires matching import authority';
          END IF;
          IF (TG_OP = 'DELETE' AND ({endpoint_old}))
             OR (TG_OP = 'UPDATE' AND ({endpoint_update_old})) THEN
            RAISE EXCEPTION 'source-owned endpoint mutation requires matching import authority';
          END IF;
          RETURN CASE WHEN TG_OP = 'DELETE' THEN OLD ELSE NEW END;
        END; $$ LANGUAGE plpgsql;
        """
    schema_editor.execute(sql)
    schema_editor.execute(f"DROP TRIGGER IF EXISTS {q(name)} ON {table}")
    schema_editor.execute(
        f"CREATE TRIGGER {q(name)} BEFORE INSERT OR UPDATE OR DELETE ON {table} "
        f"FOR EACH ROW EXECUTE FUNCTION {function}()"
    )


def _install_sqlite(
    schema_editor: Any,
    model: type[models.Model],
    declaration: ExternalOwnershipDeclaration,
    *,
    guard_key: str = "",
) -> None:
    q = schema_editor.quote_name
    table = q(model._meta.db_table)
    name = _guard_name(model._meta.db_table, guard_key)
    new_source_type = _row_value(schema_editor, model, "external_source_type", "NEW")
    new_source_id = _row_value(schema_editor, model, "external_source_id", "NEW")
    new_source_key = _row_value(schema_editor, model, "external_source_key", "NEW")
    old_source_type = _row_value(schema_editor, model, "external_source_type", "OLD")
    old_source_id = _row_value(schema_editor, model, "external_source_id", "OLD")
    old_source_key = _row_value(schema_editor, model, "external_source_key", "OLD")
    new_pk = _row_value(schema_editor, model, model._meta.pk.name, "NEW")
    old_pk = _row_value(schema_editor, model, model._meta.pk.name, "OLD")
    company_type, company_column = _company_parts(model, declaration)
    company_value = (
        "NULL" if not company_column else _row_path_value(schema_editor, model, declaration.company_field, "NEW")
    )
    company_literal = (
        "NULL"
        if not company_type
        else f"(CASE WHEN {company_value} IS NULL THEN NULL ELSE '" + company_type.replace("'", "''") + "' END)"
    )
    target = model._meta.label_lower.replace("'", "''")
    operations = "\x1f".join(sorted(declaration.operations)).replace("'", "''")
    changed = (
        " OR ".join(
            f"NEW.{q(_column(model, field))} IS NOT OLD.{q(_column(model, field))}"
            for field in sorted(declaration.source_owned_fields)
            if not model._meta.get_field(field).many_to_many
            and model._meta.get_field(field).model._meta.db_table == model._meta.db_table
        )
        or "0"
    )
    allow_new = (
        f"angee_import_scope_allows({new_source_type}, {new_source_id}, {company_literal}, "
        f"{company_value}, '{target}', {new_pk}, '{operations}')"
    )
    old_company_value = (
        "NULL" if not company_column else _row_path_value(schema_editor, model, declaration.company_field, "OLD")
    )
    old_company_literal = (
        "NULL"
        if not company_type
        else f"(CASE WHEN {old_company_value} IS NULL THEN NULL ELSE '" + company_type.replace("'", "''") + "' END)"
    )
    allow_old = (
        f"angee_import_scope_allows({old_source_type}, {old_source_id}, {old_company_literal}, "
        f"{old_company_value}, "
        f"'{target}', {old_pk}, '{operations}')"
    )
    endpoint_new = (
        " OR ".join(
            _endpoint_predicate(schema_editor, model, declaration, relation, "NEW", index)
            for index, relation in enumerate(sorted(declaration.protected_relations), start=1)
        )
        or "0"
    )
    endpoint_old = (
        " OR ".join(
            _endpoint_predicate(schema_editor, model, declaration, relation, "OLD", index)
            for index, relation in enumerate(sorted(declaration.protected_relations), start=1)
        )
        or "0"
    )
    endpoint_update_new = (
        " OR ".join(
            f"(NEW.{q(model._meta.get_field(relation).column)} IS NOT "
            f"OLD.{q(model._meta.get_field(relation).column)} AND "
            f"{_endpoint_predicate(schema_editor, model, declaration, relation, 'NEW', index)})"
            for index, relation in enumerate(sorted(declaration.protected_relations), start=1)
        )
        or "0"
    )
    endpoint_update_old = (
        " OR ".join(
            f"(NEW.{q(model._meta.get_field(relation).column)} IS NOT "
            f"OLD.{q(model._meta.get_field(relation).column)} AND "
            f"{_endpoint_predicate(schema_editor, model, declaration, relation, 'OLD', index)})"
            for index, relation in enumerate(sorted(declaration.protected_relations), start=1)
        )
        or "0"
    )
    statements = {
        "insert": (
            f"WHEN (({new_source_type} = '') <> ({new_source_id} = '') "
            f"OR ({new_source_type} = '') <> ({new_source_key} = '')) "
            f"OR ({new_source_type} <> '' AND NOT {allow_new}) OR ({endpoint_new})"
        ),
        "update": (
            f"WHEN (({new_source_type} IS NOT {old_source_type} OR {new_source_id} IS NOT {old_source_id} "
            f"OR {new_source_key} IS NOT {old_source_key}) AND NOT (COALESCE({old_source_type}, '') = '' "
            f"AND COALESCE({old_source_id}, '') = '' AND COALESCE({old_source_key}, '') = '' AND {allow_new})) "
            f"OR ({old_source_type} <> '' AND {company_value} IS NOT {old_company_value}) "
            f"OR ({old_source_type} <> '' AND ({changed}) AND NOT {allow_new}) "
            f"OR ({endpoint_update_new}) OR ({endpoint_update_old})"
        ),
        "delete": f"WHEN ({old_source_type} <> '' AND NOT {allow_old}) OR ({endpoint_old})",
    }
    for action, when in statements.items():
        trigger = q(name + "_" + action)
        schema_editor.execute(f"DROP TRIGGER IF EXISTS {trigger}")
        schema_editor.execute(
            f"CREATE TRIGGER {trigger} BEFORE {action.upper()} ON {table} FOR EACH ROW {when} "
            "BEGIN SELECT RAISE(ABORT, 'source-owned mutation requires matching import authority'); END"
        )
