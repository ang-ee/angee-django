"""Adopt retained workflows_ocr evidence into the current extraction tables.

Some composed hosts already materialized the destination app's initial graph
before this transition was added.  The old and current schemas therefore cannot
be represented by one AlterModelTable operation: both table sets can exist, and
downstream historical foreign keys can still target either set.  This migration
copies retained rows with identical primary keys into empty current tables,
keeps the old tables until their historical consumers move, and retargets public
model/resource identities transactionally.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from typing import Any, ClassVar
from uuid import NAMESPACE_URL, uuid5

from django.core.exceptions import ImproperlyConfigured
from django.core.management.color import no_style
from django.db import migrations, transaction
from django.db.migrations.state import ProjectState

from angee.base.historical_relationships import retarget_historical_resource_type

_MODELS = ("extraction", "extractionsource", "extractionpage", "extractionpart")
_OLD_TABLES = {name: f"workflows_ocr_{name}" for name in _MODELS}
_NEW_TABLES = {name: f"workflows_extraction_{name}" for name in _MODELS}
_LINEAGE_TABLE = "workflows_extraction_extractionlineage"
_RESOURCE_TYPES = {
    "workflows_ocr/extraction": "workflows_extraction/extraction",
    "workflows_ocr/extraction_source": "workflows_extraction/extraction_source",
    "workflows_ocr/extraction_page": "workflows_extraction/extraction_page",
    "workflows_ocr/extraction_part": "workflows_extraction/extraction_part",
}
_LOWER_MODEL_LABELS = {
    "workflows_ocr.extraction": "workflows_extraction.extraction",
    "workflows_ocr.extractionsource": "workflows_extraction.extractionsource",
    "workflows_ocr.extractionpage": "workflows_extraction.extractionpage",
    "workflows_ocr.extractionpart": "workflows_extraction.extractionpart",
}
_DISPLAY_MODEL_LABELS = {
    "workflows_ocr.Extraction": "workflows_extraction.Extraction",
    "workflows_ocr.ExtractionSource": "workflows_extraction.ExtractionSource",
    "workflows_ocr.ExtractionPage": "workflows_extraction.ExtractionPage",
    "workflows_ocr.ExtractionPart": "workflows_extraction.ExtractionPart",
}


def applies(project_state: ProjectState) -> bool:
    """Attach once complete retained and current extraction states coexist."""

    current = {
        ("workflows_extraction", "extraction"),
        ("workflows_extraction", "extractionlineage"),
        ("workflows_extraction", "extractionsource"),
        ("workflows_extraction", "extractionpage"),
        ("workflows_extraction", "extractionpart"),
    }
    old = {("workflows_ocr", name) for name in _MODELS}
    models = set(project_state.models)
    current_present = current & models
    old_present = old & models
    if not old_present:
        if current_present and current_present != current:
            raise ImproperlyConfigured(
                "angee.workflows_extraction:adopt_workflows_ocr found partial current model state"
            )
        return False
    if old_present == old and not current_present:
        return False
    if old_present != old or current_present != current:
        raise ImproperlyConfigured(
            "angee.workflows_extraction:adopt_workflows_ocr found partial old/current model state"
        )
    return True


def _model(apps: Any, app_label: str, model_name: str) -> Any | None:
    try:
        return apps.get_model(app_label, model_name)
    except LookupError:
        return None


def _table_columns(connection: Any, cursor: Any, table: str) -> tuple[str, ...]:
    return tuple(column.name for column in connection.introspection.get_table_description(cursor, table))


def _copy_table(
    apps: Any,
    schema_editor: Any,
    *,
    old_table: str,
    model_name: str,
    defaults: Mapping[str, Any] | None = None,
) -> None:
    connection = schema_editor.connection
    model = apps.get_model("workflows_extraction", model_name)
    supplied_defaults = dict(defaults or {})
    with connection.cursor() as cursor:
        old_columns = set(_table_columns(connection, cursor, old_table))
        target_columns = set(_table_columns(connection, cursor, model._meta.db_table))
        concrete = tuple(field for field in model._meta.concrete_fields if not field.generated)
        missing_declared = {field.column for field in concrete} - target_columns
        if missing_declared:
            raise ImproperlyConfigured(
                "angee.workflows_extraction:adopt_workflows_ocr found an incomplete "
                f"destination table {model._meta.db_table!r}: {sorted(missing_declared)!r}"
            )
        columns: list[str] = []
        selections: list[str] = []
        parameters: list[Any] = []
        for field in concrete:
            columns.append(schema_editor.quote_name(field.column))
            if field.column in old_columns:
                selections.append(schema_editor.quote_name(field.column))
                continue
            if field.attname in supplied_defaults:
                value = supplied_defaults[field.attname]
            elif field.has_default():
                value = field.get_default()
            elif field.null:
                value = None
            else:
                raise ImproperlyConfigured(
                    f"angee.workflows_extraction:adopt_workflows_ocr cannot derive "
                    f"{model._meta.label}.{field.name}"
                )
            selections.append("%s")
            parameters.append(field.get_db_prep_save(value, connection))
        cursor.execute(
            f"INSERT INTO {schema_editor.quote_name(model._meta.db_table)} "
            f"({', '.join(columns)}) SELECT {', '.join(selections)} "
            f"FROM {schema_editor.quote_name(old_table)} ORDER BY id",
            parameters,
        )


def _create_lineages(apps: Any, *, using: str) -> None:
    extraction = apps.get_model("workflows_extraction", "Extraction")
    lineage = apps.get_model("workflows_extraction", "ExtractionLineage")
    heads: dict[str, tuple[int, int]] = {}
    for pk, key, revision in extraction._base_manager.using(using).order_by(
        "lineage_key", "revision", "pk"
    ).values_list("pk", "lineage_key", "revision"):
        current = heads.get(str(key))
        candidate = (int(revision), int(pk))
        if current is None or candidate > current:
            heads[str(key)] = candidate
    lineage._base_manager.using(using).bulk_create(
        lineage(key=key, head_id=head_pk)
        for key, (_, head_pk) in sorted(heads.items())
    )


def _json_pointer_value(value: Any, pointer: str) -> Any:
    """Resolve the RFC 6901 subset used by retained extraction layouts."""

    current = value
    for raw in pointer.split("/")[1:]:
        token = raw.replace("~1", "/").replace("~0", "~")
        if isinstance(current, list):
            try:
                current = current[int(token)]
            except (ValueError, IndexError) as error:
                raise KeyError(pointer) from error
        elif isinstance(current, dict) and token in current:
            current = current[token]
        else:
            raise KeyError(pointer)
    return current


def _legacy_result_selectors(result: Any, layout: Any) -> tuple[tuple[str, tuple[str, ...]], ...]:
    """Expand the immutable layout stored with one legacy extraction result."""

    if not isinstance(result, dict) or not result:
        return ()
    if not isinstance(layout, dict):
        raise ImproperlyConfigured("Legacy extraction evidence_layout must be an object.")
    document_collection = layout.get("document_collection", "")
    line_collection = layout.get("line_collection", "")
    root_document_on_missing = layout.get("root_document_on_missing", False)
    if type(root_document_on_missing) is not bool or not all(
        isinstance(pointer, str) and (not pointer or pointer.startswith("/"))
        for pointer in (document_collection, line_collection)
    ):
        raise ImproperlyConfigured("Legacy extraction evidence_layout is invalid.")
    try:
        documents = _json_pointer_value(result, document_collection) if document_collection else None
    except KeyError:
        if not root_document_on_missing:
            raise ImproperlyConfigured("Legacy extraction result lacks its declared document collection.")
        documents = None
    if documents is not None and not isinstance(documents, list):
        raise ImproperlyConfigured("Legacy extraction document collection must be a list.")
    items = (
        tuple((f"{document_collection}/{index}", document) for index, document in enumerate(documents))
        if isinstance(documents, list)
        else (("", result),)
    )
    selectors: list[tuple[str, tuple[str, ...]]] = []
    for selector, document in items:
        if not isinstance(document, dict):
            raise ImproperlyConfigured("Legacy logical extraction documents must be objects.")
        try:
            lines = _json_pointer_value(document, line_collection) if line_collection else None
        except KeyError:
            lines = None
        if lines is not None and not isinstance(lines, list):
            raise ImproperlyConfigured("Legacy extraction line collection must be a list.")
        selectors.append((
            selector,
            tuple(f"{selector}{line_collection}/{index}" for index in range(len(lines or ()))),
        ))
    return tuple(selectors)


def _populate_document_maps(apps: Any, *, using: str) -> None:
    """Give every copied legacy result exact, stable current document identities."""

    extraction = apps.get_model("workflows_extraction", "Extraction")
    rows = extraction._base_manager.using(using)
    for row in rows.order_by("pk").only("pk", "result", "engine_config").iterator():
        config = row.engine_config
        if not isinstance(config, dict):
            raise ImproperlyConfigured(
                f"Legacy Extraction pk={row.pk} engine_config must be an object."
            )
        selectors = _legacy_result_selectors(row.result, config.get("evidence_layout", {}))
        document_map = []
        for selector, line_selectors in selectors:
            document_map.append({
                "identity": uuid5(
                    NAMESPACE_URL, f"angee://workflows_ocr/extraction/{row.pk}/document/{selector}"
                ).hex,
                "selector": selector,
                "lines": [
                    {
                        "identity": uuid5(
                            NAMESPACE_URL,
                            f"angee://workflows_ocr/extraction/{row.pk}/line/{line_selector}",
                        ).hex,
                        "selector": line_selector,
                    }
                    for line_selector in line_selectors
                ],
            })
        rows.filter(pk=row.pk).update(document_map=document_map)


def _reset_sequences(apps: Any, schema_editor: Any) -> None:
    models = [
        apps.get_model("workflows_extraction", name)
        for name in ("Extraction", "ExtractionSource", "ExtractionPage", "ExtractionPart")
    ]
    for statement in schema_editor.connection.ops.sequence_reset_sql(no_style(), models):
        schema_editor.execute(statement)


def _rewrite_versions_for_content_type(
    apps: Any,
    *,
    using: str,
    content_type_id: int,
) -> None:
    version = _model(apps, "reversion", "Version")
    if version is None:
        return
    for row in version._base_manager.using(using).select_for_update().filter(
        content_type_id=content_type_id
    ).order_by("pk"):
        if row.format != "json":
            raise ImproperlyConfigured(
                "angee.workflows_extraction:adopt_workflows_ocr cannot retarget "
                f"non-JSON Version pk={row.pk}"
            )
        try:
            payload = json.loads(row.serialized_data)
        except (TypeError, ValueError) as error:
            raise ImproperlyConfigured(
                "angee.workflows_extraction:adopt_workflows_ocr found invalid reversion JSON "
                f"for Version pk={row.pk}"
            ) from error
        if not isinstance(payload, list):
            raise ImproperlyConfigured(
                "angee.workflows_extraction:adopt_workflows_ocr expected a reversion list "
                f"for Version pk={row.pk}"
            )
        changed = False
        for entry in payload:
            if not isinstance(entry, dict):
                continue
            label = str(entry.get("model", "")).lower()
            replacement = _LOWER_MODEL_LABELS.get(label)
            if replacement is not None:
                entry["model"] = replacement
                changed = True
        if changed:
            row.serialized_data = json.dumps(payload, separators=(",", ":"))
            row.save(update_fields=["serialized_data"])


def _repoint_content_type(apps: Any, *, using: str, model_name: str) -> None:
    content_type = _model(apps, "contenttypes", "ContentType")
    if content_type is None:
        return
    rows = content_type._base_manager.using(using)
    old = rows.select_for_update().filter(app_label="workflows_ocr", model=model_name).first()
    if old is None:
        return
    _rewrite_versions_for_content_type(apps, using=using, content_type_id=old.pk)
    new = rows.select_for_update().filter(app_label="workflows_extraction", model=model_name).first()
    if new is None:
        rows.filter(pk=old.pk).update(app_label="workflows_extraction")
        return
    permission = _model(apps, "auth", "Permission")
    if permission is not None:
        permission_rows = permission._base_manager.using(using)
        for old_permission in permission_rows.select_for_update().filter(
            content_type_id=old.pk
        ).order_by("pk"):
            new_permission = permission_rows.select_for_update().filter(
                content_type_id=new.pk,
                codename=old_permission.codename,
            ).first()
            if new_permission is None:
                permission_rows.filter(pk=old_permission.pk).update(content_type_id=new.pk)
                continue
            if new_permission.name != old_permission.name:
                raise ImproperlyConfigured(
                    "angee.workflows_extraction:adopt_workflows_ocr found conflicting auth.Permission facts"
                )
            for related_model in apps.get_models(include_auto_created=True):
                for related_field in related_model._meta.concrete_fields:
                    remote = getattr(related_field, "remote_field", None)
                    if remote is None or remote.model is not permission:
                        continue
                    related_rows = related_model._base_manager.using(using).filter(
                        **{related_field.attname: old_permission.pk}
                    )
                    try:
                        if related_model._meta.auto_created:
                            for link in related_rows.select_for_update().order_by("pk"):
                                identity = {
                                    field.attname: (
                                        new_permission.pk
                                        if field is related_field
                                        else getattr(link, field.attname)
                                    )
                                    for field in related_model._meta.concrete_fields
                                    if not field.primary_key
                                }
                                duplicate = related_model._base_manager.using(using).filter(
                                    **identity
                                ).exclude(pk=link.pk).first()
                                if duplicate is not None:
                                    related_model._base_manager.using(using).filter(pk=link.pk).delete()
                                else:
                                    related_model._base_manager.using(using).filter(pk=link.pk).update(
                                        **{related_field.attname: new_permission.pk}
                                    )
                        else:
                            related_rows.update(**{related_field.attname: new_permission.pk})
                    except Exception as error:
                        raise ImproperlyConfigured(
                            "angee.workflows_extraction:adopt_workflows_ocr found a conflicting "
                            f"auth.Permission reference on {related_model._meta.label}."
                            f"{related_field.name}"
                        ) from error
            permission_rows.filter(pk=old_permission.pk).delete()

    for model in apps.get_models(include_auto_created=True):
        for field in model._meta.concrete_fields:
            remote = getattr(field, "remote_field", None)
            if remote is None or remote.model is not content_type:
                continue
            manager = model._base_manager.using(using)
            if manager.filter(**{field.attname: old.pk}).exists():
                try:
                    manager.filter(**{field.attname: old.pk}).update(**{field.attname: new.pk})
                except Exception as error:
                    raise ImproperlyConfigured(
                        "angee.workflows_extraction:adopt_workflows_ocr found a conflicting "
                        f"ContentType reference on {model._meta.label}.{field.name}"
                    ) from error
    rows.filter(pk=old.pk).delete()


def _rewrite_model_labels(apps: Any, *, using: str) -> None:
    resource = _model(apps, "resources", "Resource")
    if resource is not None:
        for old, new in _DISPLAY_MODEL_LABELS.items():
            resource._base_manager.using(using).filter(target_model__iexact=old).update(target_model=new)

    workflow = _model(apps, "workflows", "Workflow")
    if workflow is not None:
        for old, new in _LOWER_MODEL_LABELS.items():
            workflow._base_manager.using(using).filter(subject_declaration__iexact=old).update(
                subject_declaration=new
            )

    decision = _model(apps, "workflows", "Decision")
    if decision is not None:
        for old, new in _DISPLAY_MODEL_LABELS.items():
            decision._base_manager.using(using).filter(target_model__iexact=old).update(
                target_model=new
            )

    trigger = _model(apps, "workflows", "Trigger")
    if trigger is not None:
        for old, new in _LOWER_MODEL_LABELS.items():
            trigger._base_manager.using(using).filter(event_model_label__iexact=old).update(
                event_model_label=new
            )
        for row in trigger._base_manager.using(using).all().order_by("pk").iterator():
            config = row.config
            if not isinstance(config, dict):
                continue
            changed = False
            for key in ("model", "model_label"):
                value = config.get(key)
                replacement = _LOWER_MODEL_LABELS.get(str(value).lower()) if isinstance(value, str) else None
                if replacement is not None and replacement != value:
                    config[key] = replacement
                    changed = True
            if changed:
                row.save(update_fields=["config"])

    addon = _model(apps, "platform", "Addon")
    if addon is not None:
        for row in addon._base_manager.using(using).all().order_by("pk").iterator():
            labels = row.model_labels
            if not isinstance(labels, list) or not all(isinstance(value, str) for value in labels):
                raise ImproperlyConfigured(
                    "angee.workflows_extraction:adopt_workflows_ocr requires "
                    "platform.Addon.model_labels to be a list of model-label strings"
                )
            rewritten = [_LOWER_MODEL_LABELS.get(value.lower(), value) for value in labels]
            if rewritten != labels:
                row.model_labels = rewritten
                row.save(update_fields=["model_labels"])


def adopt_workflows_ocr(apps: Any, schema_editor: Any) -> None:
    """Copy one complete legacy table set and retarget its persisted identities."""

    connection = schema_editor.connection
    alias = connection.alias
    with connection.cursor() as cursor:
        tables = set(connection.introspection.table_names(cursor))
    old_present = set(_OLD_TABLES.values()) & tables
    if not old_present:
        return
    if old_present != set(_OLD_TABLES.values()):
        raise ImproperlyConfigured(
            "angee.workflows_extraction:adopt_workflows_ocr found partial legacy tables: "
            + repr(sorted(old_present))
        )
    required_targets = {*_NEW_TABLES.values(), _LINEAGE_TABLE}
    missing_targets = required_targets - tables
    if missing_targets:
        raise ImproperlyConfigured(
            "angee.workflows_extraction:adopt_workflows_ocr is missing current tables: "
            + repr(sorted(missing_targets))
        )
    with transaction.atomic(using=alias):
        with connection.cursor() as cursor:
            occupied = []
            for table in sorted(required_targets):
                cursor.execute(f"SELECT 1 FROM {schema_editor.quote_name(table)} LIMIT 1")
                if cursor.fetchone() is not None:
                    occupied.append(table)
        if occupied:
            raise ImproperlyConfigured(
                "angee.workflows_extraction:adopt_workflows_ocr refuses mixed legacy/current data: "
                + repr(occupied)
            )

        _copy_table(
            apps,
            schema_editor,
            old_table=_OLD_TABLES["extraction"],
            model_name="Extraction",
            defaults={"document_map": [], "retired_identities": []},
        )
        _populate_document_maps(apps, using=alias)
        _copy_table(
            apps,
            schema_editor,
            old_table=_OLD_TABLES["extractionsource"],
            model_name="ExtractionSource",
        )
        _copy_table(
            apps,
            schema_editor,
            old_table=_OLD_TABLES["extractionpage"],
            model_name="ExtractionPage",
        )
        _copy_table(
            apps,
            schema_editor,
            old_table=_OLD_TABLES["extractionpart"],
            model_name="ExtractionPart",
        )
        _create_lineages(apps, using=alias)
        _reset_sequences(apps, schema_editor)

        for model_name in _MODELS:
            _repoint_content_type(apps, using=alias, model_name=model_name)
        _rewrite_model_labels(apps, using=alias)
        for old_type, new_type in _RESOURCE_TYPES.items():
            retarget_historical_resource_type(
                apps,
                using=alias,
                old_type=old_type,
                new_type=new_type,
            )
        # SchemaDefinition/Relation/Permission rows stay package-managed.  The
        # migration-only app's intentionally empty permissions.zed lets the
        # normal reconcile step prune workflows_ocr rows after these grants
        # move; the current workflows_extraction source then syncs the new
        # definitions.  Reassigning those rows here would cross package
        # provenance and collide with an already-synced destination package.


class Migration(migrations.Migration):
    dependencies: ClassVar[list[tuple[str, str]]] = [
        ("workflows_extraction", "__latest__"),
        ("workflows_ocr", "__latest__"),
        ("contenttypes", "__latest__"),
        ("rebac", "__latest__"),
    ]
    operations: ClassVar[list[migrations.operations.base.Operation]] = [
        migrations.RunPython(adopt_workflows_ocr),
    ]
