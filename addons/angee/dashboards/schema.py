"""Authored dashboard snapshot API and read-only catalogue resources."""

from __future__ import annotations

import hashlib
import json
from enum import Enum
from typing import Any, cast

import strawberry
import strawberry_django
from django.apps import apps
from django.contrib.auth import get_user_model
from django.core import signing
from django.core.exceptions import ValidationError
from django.db import transaction
from django.db.models import Q
from rebac import PermissionDenied, system_context
from strawberry import auto
from strawberry.scalars import JSON

from angee.base.identity import instance_from_public_id
from angee.dashboards.models import DashboardConflictError, canonical_dashboard_snapshot
from angee.graphql.data import hasura_model_resource
from angee.graphql.ids import PublicID, require_public_id, to_public_id
from angee.graphql.node import NODE_DISPLAY_NAME_DESCRIPTION, AngeeNode
from angee.graphql.subscriptions import changes
from angee.iam.identity import user_display_label, user_public_id
from angee.iam.permissions import request_from_info, session_user

Dashboard = apps.get_model("dashboards", "Dashboard")
DashboardWidget = apps.get_model("dashboards", "DashboardWidget")


@strawberry.enum
class DashboardScope(Enum):
    PERSONAL = "personal"
    ADDON = "addon"
    RESOURCE = "resource"


@strawberry.input
class DashboardTargetInput:
    scope: DashboardScope
    key: str | None = None
    id: PublicID | None = None


@strawberry_django.type(DashboardWidget)
class DashboardWidgetType(AngeeNode):
    widget_key: auto
    definition_ref: auto
    spec_version: auto
    kind: auto
    kind_version: auto
    title: auto
    data: auto
    options: auto
    x: auto
    y: auto
    w: auto
    h: auto
    sequence: auto
    is_archived: auto


@strawberry_django.type(Dashboard)
class DashboardType(AngeeNode):
    display_name: str = strawberry_django.field(
        resolver=AngeeNode.display_name,
        only=["name"],
        description=NODE_DISPLAY_NAME_DESCRIPTION,
    )
    scope: auto
    scope_key: auto
    name: auto
    description: auto
    columns: auto
    spec_version: auto
    revision: auto
    declaration_revision: auto
    is_archived: auto
    created_at: auto
    updated_at: auto

    @strawberry_django.field(only=["owner_id"])
    def owner(self) -> strawberry.ID | None:
        return cast(strawberry.ID | None, to_public_id(get_user_model(), cast(Any, self).owner_id))

    @strawberry_django.field(only=["owner_id"])
    def owner_label(self, info: strawberry.Info) -> str | None:
        return user_display_label(cast(Any, self).owner_id, request=request_from_info(info))


@strawberry.type
class DashboardPayload:
    status: str
    id: PublicID | None = None
    revision: int | None = None
    name: str | None = None
    description: str | None = None
    snapshot: JSON | None = None
    can_edit: bool = False
    can_reset: bool = False
    can_archive: bool = False
    current_revision: int | None = None
    message: str | None = None


@strawberry.type
class DashboardSummaryType:
    id: PublicID
    scope: DashboardScope
    scope_key: str | None
    name: str
    description: str
    owner: strawberry.ID | None
    owner_label: str | None
    revision: int
    is_archived: bool
    resources: list[str]
    can_edit: bool
    can_archive: bool


@strawberry.type
class DashboardSummaryPageType:
    status: str
    version: str | None = None
    next_cursor: str | None = None
    total: int = 0
    items: list[DashboardSummaryType] = strawberry.field(default_factory=list)


def _summary_item(row: Any, info: strawberry.Info) -> DashboardSummaryType:
    sources = {
        str(widget.data.get("source", {}).get("resource"))
        for widget in row.widgets.all()
        if isinstance(widget.data, dict)
        and isinstance(widget.data.get("source"), dict)
        and widget.data["source"].get("resource")
    }
    return DashboardSummaryType(
        id=cast(PublicID, require_public_id(Dashboard, row.pk)),
        scope=DashboardScope(row.scope),
        scope_key=row.scope_key,
        name=row.name,
        description=row.description,
        owner=cast(strawberry.ID | None, user_public_id(row.owner_id)),
        owner_label=user_display_label(row.owner_id, request=request_from_info(info)),
        revision=row.revision,
        is_archived=row.is_archived,
        resources=sorted(sources),
        can_edit=row.has_access("write"),
        can_archive=row.scope == "personal" and row.has_access("archive"),
    )


def _summary_version(user: Any, items: list[DashboardSummaryType]) -> str:
    fingerprint = [
        [str(item.id), item.revision, item.can_edit, item.can_archive]
        for item in sorted(items, key=lambda value: str(value.id))
    ]
    body = json.dumps([str(user.pk), fingerprint], separators=(",", ":"), ensure_ascii=True).encode()
    return hashlib.sha256(body).hexdigest()


_SUMMARY_CURSOR_SALT = "angee.dashboards.summary-page.v1"


def _summary_cursor(user: Any, version: str, offset: int) -> str:
    return signing.dumps({"actor": str(user.pk), "version": version, "offset": offset}, salt=_SUMMARY_CURSOR_SALT)


def _summary_offset(user: Any, version: str, cursor: str | None) -> int:
    if cursor is None:
        return 0
    try:
        payload = signing.loads(cursor, salt=_SUMMARY_CURSOR_SALT)
    except signing.BadSignature as error:
        raise ValidationError({"cursor": "The dashboard summary cursor is invalid."}) from error
    if payload.get("actor") != str(user.pk) or payload.get("version") != version:
        raise ValidationError({"cursor": "The dashboard summary cursor belongs to another collection."})
    offset = payload.get("offset")
    if not isinstance(offset, int) or isinstance(offset, bool) or offset < 0:
        raise ValidationError({"cursor": "The dashboard summary cursor has an invalid offset."})
    return offset


def _snapshot(dashboard: Any) -> dict[str, Any]:
    widgets = []
    for widget in dashboard.widgets.all().order_by("sequence", "sqid"):
        widgets.append(
            {
                "schemaVersion": widget.spec_version,
                "id": widget.widget_key,
                **({"definitionRef": widget.definition_ref} if widget.definition_ref else {}),
                "kind": widget.kind,
                "kindVersion": widget.kind_version,
                "title": widget.title,
                "data": widget.data,
                "options": widget.options,
                "x": widget.x,
                "y": widget.y,
                "w": widget.w,
                "h": widget.h,
                "isArchived": widget.is_archived,
            }
        )
    return {"schemaVersion": dashboard.spec_version, "columns": dashboard.columns, "widgets": widgets}


def _payload(dashboard: Any, *, status: str = "ready") -> DashboardPayload:
    return DashboardPayload(
        status=status,
        id=cast(PublicID, require_public_id(Dashboard, dashboard.pk)),
        revision=dashboard.revision,
        name=dashboard.name,
        description=dashboard.description,
        snapshot=cast(JSON, _snapshot(dashboard)),
        can_edit=dashboard.has_access("write"),
        can_reset=dashboard.scope != "personal" and dashboard.has_access("reset"),
        can_archive=dashboard.scope == "personal" and dashboard.has_access("archive"),
    )


def _resolve_target(info: strawberry.Info, target: DashboardTargetInput) -> Any | None:
    user = session_user(info)
    if target.scope is DashboardScope.PERSONAL:
        if target.id is None:
            raise ValidationError({"target": "A personal dashboard id is required."})
        row = instance_from_public_id(Dashboard, str(target.id))
        return row if row is not None and row.scope == "personal" else None
    if not target.key:
        raise ValidationError({"target": "A scoped dashboard key is required."})
    return Dashboard.objects.filter(owner=user, scope=target.scope.value, scope_key=target.key).first()


def _target_parts(target: DashboardTargetInput, existing: Any | None) -> tuple[str, str | None]:
    if target.scope is DashboardScope.PERSONAL:
        if existing is None:
            raise ValidationError({"target": "The personal dashboard was not found."})
        return "personal", None
    if not target.key:
        raise ValidationError({"target": "A scoped dashboard key is required."})
    return target.scope.value, target.key


@strawberry.type
class DashboardQuery:
    @strawberry.field
    def dashboard(self, info: strawberry.Info, target: DashboardTargetInput) -> DashboardPayload:
        row = _resolve_target(info, target)
        if row is None:
            return DashboardPayload(status="absent" if target.scope is not DashboardScope.PERSONAL else "unavailable")
        return _payload(row)

    @strawberry.field
    def dashboard_summaries(
        self,
        info: strawberry.Info,
        cursor: str | None = None,
        version: str | None = None,
        limit: int = 100,
    ) -> DashboardSummaryPageType:
        user = session_user(info)
        if not 1 <= limit <= 100:
            raise ValidationError({"limit": "Dashboard summary pages contain from 1 to 100 items."})
        rows = list(
            Dashboard.objects.filter(Q(scope="personal") | Q(owner=user))
            .select_related("owner")
            .prefetch_related("widgets")
            .order_by("sqid")[:5_001]
        )
        if len(rows) > 5_000:
            return DashboardSummaryPageType(status="limit", total=len(rows))
        items = [_summary_item(row, info) for row in rows]
        current_version = _summary_version(user, items)
        if version is not None and version != current_version:
            return DashboardSummaryPageType(status="collection_changed", version=current_version, total=len(items))
        try:
            offset = _summary_offset(user, current_version, cursor)
        except ValidationError:
            return DashboardSummaryPageType(status="collection_changed", version=current_version, total=len(items))
        page = items[offset : offset + limit]
        next_offset = offset + len(page)
        return DashboardSummaryPageType(
            status="ready",
            version=current_version,
            next_cursor=_summary_cursor(user, current_version, next_offset) if next_offset < len(items) else None,
            total=len(items),
            items=page,
        )

@strawberry.type
class DashboardMutation:
    @strawberry.mutation
    def create_personal_dashboard(
        self,
        info: strawberry.Info,
        name: str,
        client_creation_key: str,
        description: str = "",
    ) -> DashboardPayload:
        user = session_user(info)
        row = Dashboard.objects.create_personal(
            user,
            name=name,
            description=description,
            client_creation_key=client_creation_key,
        )
        return _payload(row)

    @strawberry.mutation
    def save_dashboard(
        self,
        info: strawberry.Info,
        target: DashboardTargetInput,
        snapshot: JSON,
        persisted_id: PublicID | None = None,
        expected_revision: int | None = None,
        declaration_revision: str = "",
        name: str = "",
        description: str = "",
    ) -> DashboardPayload:
        user = session_user(info)
        existing = (
            _resolve_target(info, target)
            if persisted_id is not None or target.scope is DashboardScope.PERSONAL
            else None
        )
        if persisted_id is not None:
            addressed = instance_from_public_id(Dashboard, str(persisted_id))
            if addressed is None or existing is None or addressed.pk != existing.pk:
                return DashboardPayload(
                    status="conflict",
                    message="The persisted dashboard identity is no longer current.",
                )
            persisted_pk = addressed.pk
        else:
            persisted_pk = None
        scope, scope_key = _target_parts(target, existing)
        try:
            row = Dashboard.objects.save_snapshot(
                user,
                scope=scope,
                scope_key=scope_key,
                persisted_id=persisted_pk,
                expected_revision=expected_revision,
                snapshot=cast(Any, snapshot),
                declaration_revision=declaration_revision,
                name=name,
                description=description,
            )
        except DashboardConflictError as error:
            return DashboardPayload(status="conflict", current_revision=error.current_revision, message=str(error))
        except (ValidationError, PermissionDenied) as error:
            return DashboardPayload(status="error", message=str(error))
        return _payload(row)

    @strawberry.mutation
    def reset_dashboard(
        self,
        info: strawberry.Info,
        target: DashboardTargetInput,
        persisted_id: PublicID,
        expected_revision: int,
    ) -> DashboardPayload:
        row = _resolve_target(info, target)
        addressed = instance_from_public_id(Dashboard, str(persisted_id))
        if row is None or addressed is None or row.pk != addressed.pk:
            return DashboardPayload(status="conflict", message="The persisted dashboard identity is no longer current.")
        try:
            Dashboard.objects.reset_snapshot(row, expected_revision=expected_revision)
        except DashboardConflictError as error:
            return DashboardPayload(status="conflict", current_revision=error.current_revision, message=str(error))
        except PermissionDenied as error:
            return DashboardPayload(status="error", message=str(error))
        return DashboardPayload(status="absent")

    @strawberry.mutation
    def set_personal_dashboard_archived(
        self,
        info: strawberry.Info,
        id: PublicID,
        expected_revision: int,
        archived: bool,
    ) -> DashboardPayload:
        session_user(info)
        row = instance_from_public_id(Dashboard, str(id))
        if row is None or row.scope != "personal":
            return DashboardPayload(status="unavailable")
        if not row.has_access("archive"):
            return DashboardPayload(status="error", message="You cannot archive this dashboard.")
        with transaction.atomic(), system_context(reason="dashboards.archive"):
            locked = Dashboard.system_queryset(lock=("self",)).get(pk=row.pk)
            if locked.revision != expected_revision:
                return DashboardPayload(status="conflict", current_revision=locked.revision)
            if locked.is_archived != archived:
                locked.is_archived = archived
                locked.revision += 1
                locked.sudo(reason="dashboards.archive").save(update_fields=["is_archived", "revision"])
        return _payload(locked.with_actor(row.actor()))

    @strawberry.mutation
    def duplicate_dashboard(
        self,
        info: strawberry.Info,
        target: DashboardTargetInput,
        name: str,
        client_creation_key: str,
    ) -> DashboardPayload:
        user = session_user(info)
        source = _resolve_target(info, target)
        if source is None:
            return DashboardPayload(status="unavailable")
        created = Dashboard.objects.create_personal(
            user,
            name=name,
            client_creation_key=client_creation_key,
        )
        snapshot = _snapshot(source)
        duplicated_widgets = []
        for index, widget in enumerate(snapshot["widgets"]):
            if widget["isArchived"]:
                continue
            duplicate = {**widget, "id": f"copy-{index + 1}"}
            duplicate.pop("definitionRef", None)
            duplicated_widgets.append(duplicate)
        snapshot["widgets"] = duplicated_widgets
        canonical = canonical_dashboard_snapshot(snapshot)
        try:
            saved = Dashboard.objects.save_snapshot(
                user,
                scope="personal",
                scope_key=None,
                persisted_id=created.pk,
                expected_revision=created.revision,
                snapshot=canonical,
                name=created.name,
            )
        except DashboardConflictError as error:
            return DashboardPayload(status="conflict", current_revision=error.current_revision)
        return _payload(saved)


_DASHBOARD_RESOURCE = hasura_model_resource(
    DashboardType,
    model=Dashboard,
    name="dashboards",
    filterable=["id", "scope", "scope_key", "name", "owner", "is_archived", "updated_at"],
    sortable=["name", "scope", "updated_at"],
    aggregatable=["id"],
    groupable=["scope", "owner"],
    insert=False,
    update=False,
    delete=False,
)
_WIDGET_RESOURCE = hasura_model_resource(
    DashboardWidgetType,
    model=DashboardWidget,
    name="dashboardWidgets",
    filterable=["id", "dashboard", "kind", "is_archived"],
    sortable=["sequence", "title"],
    aggregatable=["id"],
    groupable=["kind", "dashboard"],
    insert=False,
    update=False,
    delete=False,
)

_BUCKET = {
    "query": [DashboardQuery, _DASHBOARD_RESOURCE.query, _WIDGET_RESOURCE.query],
    "mutation": [DashboardMutation, _DASHBOARD_RESOURCE.mutation, _WIDGET_RESOURCE.mutation],
    "types": [
        DashboardType,
        DashboardWidgetType,
        DashboardPayload,
        DashboardSummaryType,
        DashboardSummaryPageType,
        DashboardTargetInput,
        DashboardScope,
        *_DASHBOARD_RESOURCE.types,
        *_WIDGET_RESOURCE.types,
    ],
}

schemas = {
    "console": {
        **_BUCKET,
        "subscription": [
            changes(Dashboard, field="dashboardChanged"),
            changes(DashboardWidget, field="dashboardWidgetChanged"),
        ],
    },
}
