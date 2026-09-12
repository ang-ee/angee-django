"""Persistent copy-on-write dashboard snapshots."""

from __future__ import annotations

import json
import math
from collections.abc import Mapping, Sequence
from typing import Any, cast

from angee.base.mixins import ArchiveMixin, ArchiveQuerySet, AuditMixin
from angee.base.models import AngeeDataModel, AngeeManager, AngeeQuerySet
from django.conf import settings
from django.core.exceptions import ValidationError
from django.db import IntegrityError, models, transaction
from rebac import PermissionDenied, current_actor, system_context, to_subject_ref

DASHBOARD_SCHEMA_VERSION = 1
MAX_COLUMNS = 24
MAX_WIDGETS = 100
MAX_SNAPSHOT_BYTES = 256 * 1024
MAX_WIDGET_HEIGHT = 100
MAX_LAYOUT_ROWS = 1_000
MAX_FILTER_DEPTH = 10
MAX_FILTER_CLAUSES = 100


class DashboardConflictError(Exception):
    """The caller's persisted identity or revision is no longer current."""

    def __init__(self, current_revision: int | None = None) -> None:
        self.current_revision = current_revision
        super().__init__("The dashboard changed since it was loaded.")


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
            "schemaVersion", "id", "definitionRef", "kind", "kindVersion", "title",
            "data", "options", "x", "y", "w", "h", "isArchived",
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
        data = raw.get("data")
        if not isinstance(data, dict) or data.get("shape") not in {"value", "series", "rows", "none"}:
            raise ValidationError({"snapshot": f"{path}.data has an invalid shape."})
        if data["shape"] == "none":
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
        packed = {**widget, "x": x, "y": y}
        placed.append(packed)
        packed_by_id[widget["id"]] = packed
    canonical = [packed_by_id.get(widget["id"], widget) for widget in validated]
    return {"schemaVersion": DASHBOARD_SCHEMA_VERSION, "columns": columns, "widgets": canonical}


def _invalid_query(path: str, message: str) -> None:
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
                "hasKey", "contains", "iContains", "startsWith", "iStartsWith",
                "endsWith", "iEndsWith", "like", "iLike", "notLike", "notILike",
                "similar", "notSimilar", "regex", "iRegex", "notRegex", "notIRegex",
            }
            if operator in string_operators and not isinstance(operand, str):
                _invalid_query(f"{path}.{field_name}.{operator}", "operand must be a string")
            if isinstance(operand, float) and not math.isfinite(operand):
                _invalid_query(f"{path}.{field_name}.{operator}", "operand must be finite")


def validate_dashboard_queries(snapshot: Mapping[str, Any]) -> None:
    """Validate every active widget query against the composed console contract."""

    from angee.graphql.schema import GraphQLSchemas

    resources: dict[str, Any] = {}
    for resource in GraphQLSchemas.from_discovery().resources("console"):
        for label in (resource.model_label, resource.canonical_label):
            if label:
                resources[label] = resource
                resources[label.casefold()] = resource

    for index, widget in enumerate(snapshot["widgets"]):
        if widget["isArchived"] or widget["data"]["shape"] == "none":
            continue
        path = f"widgets[{index}].data"
        data = widget["data"]
        if set(data) != {"shape", "source"}:
            _invalid_query(path, "query widgets accept only shape and source")
        source = data["source"]
        allowed_source_keys = {"resource", "filter", "groups", "sort", "measure", "fields", "limit", "refresh"}
        unknown_source_keys = set(source) - allowed_source_keys
        if unknown_source_keys:
            _invalid_query(f"{path}.source", f"unknown keys: {', '.join(sorted(unknown_source_keys))}")
        resource_name = source["resource"]
        resource = resources.get(resource_name) or resources.get(resource_name.casefold())
        if resource is None:
            _invalid_query(f"{path}.source.resource", f'unknown console resource "{resource_name}"')

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

        limit = source.get("limit")
        if limit is not None and (
            not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 100
        ):
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


class DashboardQuerySet(ArchiveQuerySet[Any], AngeeQuerySet[Any]):
    """Archive scopes layered over actor-scoped dashboard reads."""


class DashboardManager(AngeeManager.from_queryset(DashboardQuerySet)):
    """The sole snapshot write owner, including CAS and child diffs."""

    def for_target(self, owner: Any, scope: str, scope_key: str | None) -> Any | None:
        if scope == "personal":
            raise ValueError("Personal dashboards resolve by public id.")
        return self.filter(owner=owner, scope=scope, scope_key=scope_key).first()

    def create_personal(
        self,
        owner: Any,
        *,
        name: str,
        description: str = "",
        client_creation_key: str,
    ) -> Any:
        actor = self.check_create({"owner": (owner,)})
        if to_subject_ref(owner) != actor:
            raise PermissionDenied("A personal dashboard can only be created for the acting user.")
        if not client_creation_key:
            raise ValidationError({"client_creation_key": "A client creation key is required."})
        existing = self.filter(owner=owner, client_creation_key=client_creation_key).first()
        if existing is not None:
            return existing
        dashboard = self.model(
            owner=owner,
            scope="personal",
            scope_key=None,
            name=name.strip() or "Untitled dashboard",
            description=description,
            client_creation_key=client_creation_key,
        )
        try:
            with transaction.atomic():
                dashboard.full_clean()
                dashboard.sudo(reason="dashboards.create_personal").save()
        except IntegrityError:
            return self.get(owner=owner, client_creation_key=client_creation_key)
        return dashboard.with_actor(actor)

    def save_snapshot(
        self,
        owner: Any,
        *,
        scope: str,
        scope_key: str | None,
        persisted_id: int | None,
        expected_revision: int | None,
        snapshot: Any,
        declaration_revision: str = "",
        name: str = "",
        description: str = "",
    ) -> Any:
        canonical = canonical_dashboard_snapshot(snapshot)
        validate_dashboard_queries(canonical)
        actor = current_actor()
        if actor is None:
            raise PermissionDenied("Dashboard snapshots require an acting user.")
        if scope not in {"personal", "addon", "resource"}:
            raise ValidationError({"scope": "Unknown dashboard scope."})
        with transaction.atomic(), system_context(reason="dashboards.save_snapshot"):
            current = None
            if persisted_id is not None:
                current = cast(Any, self.model).system_queryset(lock=("self",)).filter(pk=persisted_id).first()
                if current is None:
                    raise DashboardConflictError()
                if scope != "personal" and current.owner_id != owner.pk:
                    raise DashboardConflictError()
                if current.revision != expected_revision:
                    raise DashboardConflictError(current.revision)
                if current.scope != scope or current.scope_key != scope_key:
                    raise DashboardConflictError(current.revision)
            else:
                if to_subject_ref(owner) != actor:
                    raise PermissionDenied("New scoped dashboards can only be created for the acting user.")
                if expected_revision is not None:
                    raise DashboardConflictError()
                if scope == "personal":
                    raise ValidationError({"scope": "Create personal dashboards before saving them."})
                current = cast(Any, self.model).system_queryset(lock=("self",)).filter(
                    owner=owner, scope=scope, scope_key=scope_key,
                ).first()
                if current is not None:
                    raise DashboardConflictError(current.revision)
                current = self.model(
                    owner=owner,
                    scope=scope,
                    scope_key=scope_key,
                    name=name or scope_key or "Dashboard",
                    description=description,
                    columns=canonical["columns"],
                    declaration_revision=declaration_revision,
                )
                current.full_clean()
                try:
                    with transaction.atomic():
                        current.sudo(reason="dashboards.materialize").save()
                except IntegrityError as error:
                    raise DashboardConflictError() from error

            if current.scope != "personal" and to_subject_ref(current.owner) != actor:
                raise PermissionDenied("Scoped dashboards can only be edited by their owner.")
            if not current.with_actor(actor).has_access("write"):
                raise PermissionDenied("You cannot edit this dashboard.")
            widget_model = current._meta.get_field("widgets").related_model
            existing_widgets = {
                widget.widget_key: widget
                for widget in widget_model.system_queryset(lock=("self",)).filter(dashboard=current)
            }
            changed = current.columns != canonical["columns"] or current.declaration_revision != declaration_revision
            dashboard_fields: list[str] = []
            if name and current.name != name:
                current.name = name
                dashboard_fields.append("name")
            if current.description != description:
                current.description = description
                dashboard_fields.append("description")
            changed = changed or bool(dashboard_fields)
            seen: set[str] = set()
            for sequence, item in enumerate(canonical["widgets"]):
                seen.add(item["id"])
                widget = existing_widgets.get(item["id"])
                fields = {
                    "definition_ref": item.get("definitionRef"),
                    "spec_version": item["schemaVersion"],
                    "kind": item["kind"],
                    "kind_version": item["kindVersion"],
                    "title": item["title"],
                    "data": item["data"],
                    "options": item["options"],
                    "x": item["x"], "y": item["y"], "w": item["w"], "h": item["h"],
                    "sequence": sequence,
                    "is_archived": item["isArchived"],
                }
                if widget is None:
                    widget = widget_model(dashboard=current, widget_key=item["id"], **fields)
                    widget.full_clean()
                    widget.sudo(reason="dashboards.widget.create").save()
                    changed = True
                else:
                    dirty = [field for field, value in fields.items() if getattr(widget, field) != value]
                    if dirty:
                        for field in dirty:
                            setattr(widget, field, fields[field])
                        widget.full_clean()
                        widget.sudo(reason="dashboards.widget.update").save(update_fields=dirty)
                        changed = True
            for key, widget in existing_widgets.items():
                if key not in seen and not widget.is_archived:
                    widget.is_archived = True
                    widget.sudo(reason="dashboards.widget.archive").save(update_fields=["is_archived"])
                    changed = True
            if changed:
                current.columns = canonical["columns"]
                current.declaration_revision = declaration_revision
                current.revision += 1
                current.sudo(reason="dashboards.save_snapshot").save(
                    update_fields=["columns", "declaration_revision", "revision", *dashboard_fields],
                )
            return current.with_actor(actor)

    def reset_snapshot(self, dashboard: Any, *, expected_revision: int) -> None:
        actor = current_actor()
        with transaction.atomic(), system_context(reason="dashboards.reset_snapshot"):
            locked = cast(Any, self.model).system_queryset(lock=("self",)).get(pk=dashboard.pk)
            if locked.revision != expected_revision:
                raise DashboardConflictError(locked.revision)
            if locked.scope == "personal" or actor is None or to_subject_ref(locked.owner) != actor:
                raise PermissionDenied("Only the owner can reset a scoped dashboard.")
            locked.sudo(reason="dashboards.reset_snapshot").delete()


DashboardObjects = DashboardManager()


class Dashboard(ArchiveMixin, AuditMixin, AngeeDataModel):
    """One actor-owned complete dashboard snapshot."""

    runtime = True
    sqid_prefix = "dsh_"
    rebac_grantable = {"viewer": "share", "editor": "share"}

    class Scope(models.TextChoices):
        PERSONAL = "personal", "Personal"
        ADDON = "addon", "Addon"
        RESOURCE = "resource", "Resource"

    owner = models.ForeignKey(settings.AUTH_USER_MODEL, on_delete=models.PROTECT, related_name="dashboards")
    scope = models.CharField(max_length=16, choices=Scope, db_index=True)
    scope_key = models.CharField(max_length=255, null=True, blank=True)
    name = models.CharField(max_length=200)
    description = models.TextField(blank=True, default="")
    columns = models.PositiveSmallIntegerField(default=12)
    spec_version = models.PositiveSmallIntegerField(default=DASHBOARD_SCHEMA_VERSION)
    revision = models.PositiveIntegerField(default=1)
    declaration_revision = models.CharField(max_length=128, blank=True, default="")
    client_creation_key = models.CharField(max_length=128, null=True, blank=True)

    objects = DashboardObjects

    class Meta:
        abstract = True
        ordering = ("name", "sqid")
        rebac_resource_type = "dashboards/dashboard"
        rebac_id_attr = "sqid"
        constraints = (
            models.CheckConstraint(
                condition=(
                    models.Q(scope="personal", scope_key__isnull=True)
                    | (
                        models.Q(scope__in=("addon", "resource"))
                        & models.Q(scope_key__isnull=False)
                        & ~models.Q(scope_key="")
                    )
                ),
                name="dashboard_scope_key_shape",
            ),
            models.CheckConstraint(
                condition=models.Q(columns__gte=1, columns__lte=MAX_COLUMNS),
                name="dashboard_columns_range",
            ),
            models.CheckConstraint(condition=models.Q(revision__gte=1), name="dashboard_revision_positive"),
            models.CheckConstraint(
                condition=(models.Q(scope="personal") | models.Q(is_archived=False)),
                name="dashboard_scoped_not_archived",
            ),
            models.UniqueConstraint(
                fields=("owner", "scope", "scope_key"),
                condition=~models.Q(scope="personal"),
                name="dashboard_owner_scoped_target",
            ),
            models.UniqueConstraint(
                fields=("owner", "client_creation_key"),
                condition=models.Q(client_creation_key__isnull=False),
                name="dashboard_owner_creation_key",
            ),
        )

    def __str__(self) -> str:
        return self.name


class DashboardWidgetQuerySet(ArchiveQuerySet[Any], AngeeQuerySet[Any]):
    """Archive scopes layered over dashboard widget reads."""


DashboardWidgetManager = AngeeManager.from_queryset(DashboardWidgetQuerySet)


class DashboardWidget(ArchiveMixin, AuditMixin, AngeeDataModel):
    """One versioned widget specification inside a snapshot."""

    runtime = True
    sqid_prefix = "dsw_"

    dashboard = models.ForeignKey("dashboards.Dashboard", on_delete=models.CASCADE, related_name="widgets")
    widget_key = models.CharField(max_length=128)
    definition_ref = models.CharField(max_length=128, null=True, blank=True)
    spec_version = models.PositiveSmallIntegerField(default=DASHBOARD_SCHEMA_VERSION)
    kind = models.CharField(max_length=128)
    kind_version = models.PositiveSmallIntegerField(default=1)
    title = models.CharField(max_length=240)
    data = models.JSONField()
    options = models.JSONField(default=dict, blank=True)
    x = models.PositiveSmallIntegerField(default=0)
    y = models.PositiveSmallIntegerField(default=0)
    w = models.PositiveSmallIntegerField(default=1)
    h = models.PositiveSmallIntegerField(default=1)
    sequence = models.PositiveSmallIntegerField(default=0)

    objects = DashboardWidgetManager()

    class Meta:
        abstract = True
        ordering = ("sequence", "sqid")
        rebac_resource_type = "dashboards/widget"
        rebac_id_attr = "sqid"
        constraints = (
            models.UniqueConstraint(fields=("dashboard", "widget_key"), name="dashboard_widget_key"),
            models.UniqueConstraint(
                fields=("dashboard", "definition_ref"),
                condition=models.Q(definition_ref__isnull=False),
                name="dashboard_widget_definition_ref",
            ),
            models.CheckConstraint(
                condition=models.Q(w__gte=1, h__gte=1, h__lte=MAX_WIDGET_HEIGHT),
                name="dashboard_widget_size",
            ),
            models.CheckConstraint(condition=models.Q(y__lte=MAX_LAYOUT_ROWS), name="dashboard_widget_row"),
        )

    def __str__(self) -> str:
        return self.title
