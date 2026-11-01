"""Dashboard snapshot values, layout and declared query admission."""

from __future__ import annotations

import json
import math
from collections.abc import Mapping, Sequence
from typing import Any, NoReturn

from django.apps import apps
from django.core.exceptions import FieldDoesNotExist, ValidationError
from django.db import models

from angee.iam.graphql.access import is_gated_read_axis
from angee.graphql.data.hasura import declared_hasura_resource_fields
from angee.graphql.introspection import FieldPathError, require_field_for_path
from angee.graphql.schema import GraphQLSchemas
from angee.iam.permissions import effective_rebac_definition, require_rebac_queryset

DASHBOARD_SCHEMA_VERSION = 1
MAX_COLUMNS = 24
MAX_WIDGETS = 100
MAX_SNAPSHOT_BYTES = 256 * 1024
MAX_WIDGET_HEIGHT = 100
MAX_LAYOUT_ROWS = 1_000
MAX_FILTER_DEPTH = 10
MAX_FILTER_CLAUSES = 100


def widget_visibility_answers(policies: Sequence[Mapping[str, Any]], actor: Any) -> list[bool]:
    """Batch declared listing scopes, never infer authority from result counts.

    ``resource`` declares the container-scope ``key`` (for example
    ``{"resource": "projects.Task", "key": "queue__slug", "value": "incoming"}``).
    Use the Task resource that declares the key, not the related Queue resource.
    Listing admission reads that container, never a source row. The source
    query still enforces its own row permissions. Unknown/invalid policies fail
    validation; an inaccessible or missing scope yields false.
    """

    scopes: dict[tuple[type[models.Model], str], list[tuple[int, Any]]] = {}
    for index, policy in enumerate(policies):
        if set(policy) != {"resource", "key", "value"} or not all(
            isinstance(value, str) and value for value in policy.values()
        ):
            raise ValidationError({"visibility": "Declare a resource, key and non-empty value."})
        try:
            model = apps.get_model(policy["resource"])
            key = policy["key"].replace(".", "__")
            if "." in policy["key"] or key not in declared_hasura_resource_fields(
                model, "hasura_container_scope_fields"
            ):
                raise ValidationError({"visibility": "Use a declared container scope key."})
            field = require_field_for_path(model, key)
            container_path, separator, _leaf = key.rpartition("__")
            container = require_field_for_path(model, container_path).related_model if separator else model
        except (LookupError, ValueError, FieldDoesNotExist, FieldPathError) as error:
            raise ValidationError({"visibility": "The scope resource or key does not exist."}) from error
        if container is None or not isinstance(field, models.Field):
            raise ValidationError({"visibility": "Use a scalar key on a related scope."})
        definition = effective_rebac_definition(container)
        if field.is_relation or not field.unique or definition is None or is_gated_read_axis(model, key):
            raise ValidationError({"visibility": "Use a unique, ungated scalar key on a permission-managed scope."})
        scopes.setdefault((container, field.name), []).append((index, field.to_python(policy["value"])))
    allowed = [False] * len(policies)
    for (model, key), entries in scopes.items():
        rows = require_rebac_queryset(model).with_actor(actor) if actor is not None else model._base_manager.none()
        readable = set(rows.filter(**{f"{key}__in": [value for _, value in entries]}).values_list(key, flat=True))
        for index, value in entries:
            allowed[index] = value in readable
    return allowed


def _as_int(value: Any, path: str, *, positive: bool = False) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        raise ValidationError({"snapshot": f"{path} must be an integer."})
    if (positive and value <= 0) or (not positive and value < 0):
        raise ValidationError({"snapshot": f"{path} is outside the allowed range."})
    return value


def _overlaps(left: Mapping[str, Any], right: Mapping[str, Any]) -> bool:
    return (
        left["x"] < right["x"] + right["w"]
        and right["x"] < left["x"] + left["w"]
        and left["y"] < right["y"] + right["h"]
        and right["y"] < left["y"] + left["h"]
    )


def _first_slot(placed: Sequence[Mapping[str, Any]], width: int, height: int, columns: int) -> tuple[int, int]:
    bottom = max((int(item["y"]) + int(item["h"]) for item in placed), default=0)
    for y in range(bottom + 1):
        for x in range(columns - width + 1):
            probe = {"id": "__probe__", "x": x, "y": y, "w": width, "h": height}
            if not any(_overlaps(probe, item) for item in placed):
                return x, y
    return 0, bottom


def canonical_dashboard_snapshot(value: Any) -> dict[str, Any]:
    """Validate and canonically pack one complete versioned snapshot."""

    if not isinstance(value, dict) or value.get("schemaVersion") != DASHBOARD_SCHEMA_VERSION:
        raise ValidationError({"snapshot": "Unsupported dashboard schema version."})
    if set(value) != {"schemaVersion", "columns", "widgets"}:
        raise ValidationError({"snapshot": "Snapshot contains unknown fields."})
    columns = _as_int(value.get("columns"), "columns", positive=True)
    if columns > MAX_COLUMNS:
        raise ValidationError({"snapshot": f"columns must be at most {MAX_COLUMNS}."})
    widgets = value.get("widgets")
    if not isinstance(widgets, list) or len(widgets) > MAX_WIDGETS:
        raise ValidationError({"snapshot": f"widgets must contain at most {MAX_WIDGETS} items."})
    encoded = json.dumps(value, separators=(",", ":"), ensure_ascii=False).encode()
    if len(encoded) > MAX_SNAPSHOT_BYTES:
        raise ValidationError({"snapshot": f"snapshot must be at most {MAX_SNAPSHOT_BYTES} bytes."})

    ids: set[str] = set()
    validated: list[dict[str, Any]] = []
    for index, raw in enumerate(widgets):
        path = f"widgets[{index}]"
        if not isinstance(raw, dict):
            raise ValidationError({"snapshot": f"{path} must be an object."})
        allowed_widget_keys = {
            "schemaVersion",
            "id",
            "definitionRef",
            "kind",
            "kindVersion",
            "title",
            "visibility",
            "data",
            "options",
            "x",
            "y",
            "w",
            "h",
            "isArchived",
        }
        if set(raw) - allowed_widget_keys:
            raise ValidationError({"snapshot": f"{path} contains unknown fields."})
        widget_id = raw.get("id")
        if not isinstance(widget_id, str) or not widget_id or widget_id in ids:
            raise ValidationError({"snapshot": f"{path}.id must be non-empty and unique."})
        ids.add(widget_id)
        if raw.get("schemaVersion") != DASHBOARD_SCHEMA_VERSION:
            raise ValidationError({"snapshot": f"{path}.schemaVersion is unsupported."})
        if not isinstance(raw.get("kind"), str) or not raw["kind"]:
            raise ValidationError({"snapshot": f"{path}.kind is required."})
        _as_int(raw.get("kindVersion"), f"{path}.kindVersion", positive=True)
        if not isinstance(raw.get("title"), str) or not isinstance(raw.get("options"), dict):
            raise ValidationError({"snapshot": f"{path} has invalid title or options."})
        if "visibility" in raw:
            if not isinstance(raw["visibility"], dict):
                raise ValidationError({"visibility": "The widget visibility policy must be an object."})
            widget_visibility_answers([raw["visibility"]], None)
        data = raw.get("data")
        if not isinstance(data, dict) or data.get("shape") not in {"value", "series", "rows", "none", "resourceView"}:
            raise ValidationError({"snapshot": f"{path}.data has an invalid shape."})
        if data["shape"] == "resourceView":
            if set(data) != {"shape", "preset"} or not isinstance(data.get("preset"), str) or not data["preset"]:
                raise ValidationError({"snapshot": f"{path}.data requires a resource-view preset."})
            route = raw["options"].get("fullViewRoute")
            if not isinstance(route, str) or not route.strip():
                raise ValidationError({"snapshot": f"{path}.options.fullViewRoute is required."})
        elif data["shape"] == "none":
            binding = data.get("binding")
            if (
                set(data) != {"shape", "binding"}
                or not isinstance(binding, dict)
                or set(binding) != {"dashboardKey", "widgetId"}
                or not all(isinstance(binding.get(key), str) and binding[key] for key in ("dashboardKey", "widgetId"))
            ):
                raise ValidationError({"snapshot": f"{path}.data requires a binding and no source."})
        elif (
            set(data) != {"shape", "source"}
            or not isinstance(data.get("source"), dict)
            or not isinstance(data["source"].get("resource"), str)
            or not data["source"]["resource"]
        ):
            raise ValidationError({"snapshot": f"{path}.data.source.resource is required."})
        rect = {
            "x": _as_int(raw.get("x"), f"{path}.x"),
            "y": _as_int(raw.get("y"), f"{path}.y"),
            "w": _as_int(raw.get("w"), f"{path}.w", positive=True),
            "h": _as_int(raw.get("h"), f"{path}.h", positive=True),
        }
        if rect["w"] > columns or rect["h"] > MAX_WIDGET_HEIGHT or rect["y"] + rect["h"] > MAX_LAYOUT_ROWS:
            raise ValidationError({"snapshot": f"{path} exceeds dashboard layout limits."})
        archived = raw.get("isArchived")
        if not isinstance(archived, bool):
            raise ValidationError({"snapshot": f"{path}.isArchived must be a boolean."})
        validated.append({**raw, **rect})

    placed: list[dict[str, Any]] = []
    active = sorted(
        (widget for widget in validated if not widget["isArchived"]),
        key=lambda item: (item["y"], item["x"], item["id"]),
    )
    packed_by_id: dict[str, dict[str, Any]] = {}
    for widget in active:
        x, y = _first_slot(placed, widget["w"], widget["h"], columns)
        if y + widget["h"] > MAX_LAYOUT_ROWS:
            raise ValidationError({"snapshot": "Packed widgets exceed dashboard layout limits."})
        packed = {**widget, "x": x, "y": y}
        placed.append(packed)
        packed_by_id[widget["id"]] = packed
    canonical = [packed_by_id.get(widget["id"], widget) for widget in validated]
    return {"schemaVersion": DASHBOARD_SCHEMA_VERSION, "columns": columns, "widgets": canonical}


def _invalid_query(path: str, message: str) -> NoReturn:
    raise ValidationError({"snapshot": f"{path}: {message}"})


def _validate_filter(value: Any, resource: Any, path: str, *, depth: int = 0, clauses: list[int] | None = None) -> None:
    if depth > MAX_FILTER_DEPTH:
        _invalid_query(path, f"filter nesting must be at most {MAX_FILTER_DEPTH} levels")
    if not isinstance(value, dict):
        _invalid_query(path, "filter must be an object")
    counter = clauses if clauses is not None else [0]
    for field_name, raw_lookup in value.items():
        if field_name in {"AND", "OR"}:
            branches = raw_lookup if isinstance(raw_lookup, list) else [raw_lookup]
            if not branches:
                _invalid_query(f"{path}.{field_name}", "logical branches cannot be empty")
            for index, branch in enumerate(branches):
                _validate_filter(branch, resource, f"{path}.{field_name}[{index}]", depth=depth + 1, clauses=counter)
            continue
        if field_name == "NOT":
            _validate_filter(raw_lookup, resource, f"{path}.NOT", depth=depth + 1, clauses=counter)
            continue
        field = resource.query.fields.get(field_name)
        if field is None or field.filter is None:
            _invalid_query(f"{path}.{field_name}", "field is not filterable")
        lookup = (
            raw_lookup
            if isinstance(raw_lookup, dict)
            else {"isNull": True}
            if raw_lookup is None
            else {"inList": raw_lookup}
            if isinstance(raw_lookup, list)
            else {"exact": raw_lookup}
        )
        if not lookup:
            _invalid_query(f"{path}.{field_name}", "at least one comparison is required")
        for operator, operand in lookup.items():
            counter[0] += 1
            if counter[0] > MAX_FILTER_CLAUSES:
                _invalid_query(path, f"filters may contain at most {MAX_FILTER_CLAUSES} comparisons")
            if operator not in field.filter.operators:
                _invalid_query(f"{path}.{field_name}.{operator}", "operator is not available for this field")
            if operator == "isNull" and not isinstance(operand, bool):
                _invalid_query(f"{path}.{field_name}.{operator}", "operand must be a boolean")
            if operator in {"inList", "notInList", "hasKeysAny", "hasKeysAll"} and not isinstance(operand, list):
                _invalid_query(f"{path}.{field_name}.{operator}", "operand must be an array")
            string_operators = {
                "hasKey",
                "contains",
                "iContains",
                "startsWith",
                "iStartsWith",
                "endsWith",
                "iEndsWith",
                "like",
                "iLike",
                "notLike",
                "notILike",
                "similar",
                "notSimilar",
                "regex",
                "iRegex",
                "notRegex",
                "notIRegex",
            }
            if operator in string_operators and not isinstance(operand, str):
                _invalid_query(f"{path}.{field_name}.{operator}", "operand must be a string")
            if isinstance(operand, float) and not math.isfinite(operand):
                _invalid_query(f"{path}.{field_name}.{operator}", "operand must be finite")


def validate_dashboard_queries(snapshot: Mapping[str, Any]) -> None:
    """Validate active queries and declared row columns against the console contract."""

    resources: dict[str, Any] = {}
    for resource in GraphQLSchemas.from_discovery().resources("console"):
        for label in (resource.model_label, resource.canonical_label):
            if label:
                resources[label] = resource
                resources[label.casefold()] = resource

    for index, widget in enumerate(snapshot["widgets"]):
        if widget["isArchived"]:
            continue
        path = f"widgets[{index}].data"
        data = widget["data"]
        if data["shape"] != "rows" and "columns" in widget["options"]:
            _invalid_query(f"widgets[{index}].options.columns", "columns are only valid for row widgets")
        if data["shape"] in {"none", "resourceView"}:
            continue
        if set(data) != {"shape", "source"}:
            _invalid_query(path, "query widgets accept only shape and source")
        source = data["source"]
        allowed_source_keys = {"resource", "filter", "groups", "sort", "measure", "fields", "limit", "refresh"}
        unknown_source_keys = set(source) - allowed_source_keys
        if unknown_source_keys:
            _invalid_query(f"{path}.source", f"unknown keys: {', '.join(sorted(unknown_source_keys))}")
        resource_name = source["resource"]
        matched_resource = resources.get(resource_name) or resources.get(resource_name.casefold())
        if matched_resource is None:
            _invalid_query(f"{path}.source.resource", f'unknown console resource "{resource_name}"')
        resource = matched_resource

        shape = data["shape"]
        if shape == "value" and resource.roots.aggregate_name is None:
            _invalid_query(path, "resource has no aggregate operation")
        if shape == "series" and (resource.roots.group_name is None or resource.roots.group_count_name is None):
            _invalid_query(path, "resource has no complete grouped aggregate operation")
        if shape == "rows" and (resource.roots.list_name is None or resource.roots.aggregate_name is None):
            _invalid_query(path, "resource has no complete list operation")

        filter_value = source.get("filter")
        if filter_value is not None:
            _validate_filter(filter_value, resource, f"{path}.source.filter")

        groups = source.get("groups", [])
        if not isinstance(groups, list):
            _invalid_query(f"{path}.source.groups", "groups must be an array")
        if shape == "series" and len(groups) != 1:
            _invalid_query(f"{path}.source.groups", "series widgets require exactly one group")
        if shape != "series" and groups:
            _invalid_query(f"{path}.source.groups", "groups are only valid for series widgets")
        seen_groups: set[str] = set()
        for group_index, group in enumerate(groups):
            group_path = f"{path}.source.groups[{group_index}]"
            if not isinstance(group, dict) or set(group) - {"field", "granularity"}:
                _invalid_query(group_path, "group has an invalid shape")
            axis = resource.query.axes.get(group.get("field"))
            if axis is None or axis.server is None:
                _invalid_query(f"{group_path}.field", "axis does not support server grouping")
            group_id = f"{group['field']}:{group.get('granularity', '')}"
            if group_id in seen_groups:
                _invalid_query(group_path, "group is duplicated")
            seen_groups.add(group_id)
            granularity = group.get("granularity")
            if granularity and granularity not in {item.name for item in axis.extractions}:
                _invalid_query(f"{group_path}.granularity", "extraction is not available for this axis")

        measure = source.get("measure", {"op": "count"})
        if not isinstance(measure, dict) or set(measure) - {"op", "field"}:
            _invalid_query(f"{path}.source.measure", "measure has an invalid shape")
        op = measure.get("op")
        field_name = measure.get("field")
        if op == "count":
            if field_name is not None:
                _invalid_query(f"{path}.source.measure.field", "count does not accept a field")
        elif not any(item.op == op and item.field == field_name for item in resource.aggregate_measures):
            _invalid_query(f"{path}.source.measure", "measure is not exposed by this resource")

        fields = source.get("fields", [resource.query.identity.field] if shape == "rows" else [])
        if not isinstance(fields, list) or any(not isinstance(item, str) for item in fields):
            _invalid_query(f"{path}.source.fields", "fields must be an array of names")
        if shape == "rows" and not fields:
            _invalid_query(f"{path}.source.fields", "row widgets require at least one field")
        if shape != "rows" and fields:
            _invalid_query(f"{path}.source.fields", "fields are only valid for row widgets")
        for field_name in fields:
            field = resource.query.fields.get(field_name)
            if field is None or field.row is None:
                _invalid_query(f"{path}.source.fields", f'field "{field_name}" has no readable row projection')

        if shape == "rows" and "columns" in widget["options"]:
            columns_path = f"widgets[{index}].options.columns"
            columns = widget["options"]["columns"]
            if not isinstance(columns, list) or not columns:
                _invalid_query(columns_path, "columns must be a non-empty array")
            seen_columns: set[str] = set()
            for column_index, column in enumerate(columns):
                column_path = f"{columns_path}[{column_index}]"
                if (
                    not isinstance(column, dict)
                    or set(column) - {"path", "label"}
                    or not isinstance(column.get("path"), str)
                    or not column["path"]
                    or ("label" in column and (not isinstance(column["label"], str) or not column["label"]))
                ):
                    _invalid_query(column_path, "column requires a path and an optional non-empty string label")
                if column["path"] not in fields:
                    _invalid_query(f"{column_path}.path", "column must be selected in source.fields")
                if column["path"] in seen_columns:
                    _invalid_query(f"{column_path}.path", "column is duplicated")
                seen_columns.add(column["path"])

        limit = source.get("limit")
        if limit is not None and (not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 100):
            _invalid_query(f"{path}.source.limit", "limit must be an integer from 1 to 100")

        sort = source.get("sort", [])
        if not isinstance(sort, list):
            _invalid_query(f"{path}.source.sort", "sort must be an array")
        if shape != "rows" and sort:
            _invalid_query(f"{path}.source.sort", "sort is only valid for row widgets")
        for sort_index, term in enumerate(sort):
            term_path = f"{path}.source.sort[{sort_index}]"
            if (
                not isinstance(term, dict)
                or set(term) != {"field", "direction"}
                or term["direction"] not in {"ASC", "DESC"}
            ):
                _invalid_query(term_path, "sort term has an invalid shape")
            field = resource.query.fields.get(term["field"])
            if field is None or field.sort is None:
                _invalid_query(f"{term_path}.field", "field is not sortable")

        refresh = source.get("refresh")
        if refresh is not None:
            if not isinstance(refresh, dict) or refresh.get("mode") not in {"live", "interval", "manual"}:
                _invalid_query(f"{path}.source.refresh", "refresh policy has an invalid shape")
            expected_keys = {"mode", "seconds"} if refresh.get("mode") == "interval" else {"mode"}
            if set(refresh) != expected_keys:
                _invalid_query(f"{path}.source.refresh", "refresh policy has unexpected fields")
            if refresh.get("mode") == "interval" and (
                not isinstance(refresh.get("seconds"), int)
                or isinstance(refresh.get("seconds"), bool)
                or not 5 <= refresh["seconds"] <= 3_600
            ):
                _invalid_query(f"{path}.source.refresh.seconds", "interval must be from 5 to 3600 seconds")
