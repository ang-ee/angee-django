"""Console records and thin manual backup actions."""

from __future__ import annotations

from datetime import datetime

import strawberry
import strawberry_django
from django.apps import apps
from django.utils import timezone
from rebac import current_actor
from strawberry import auto
from strawberry.scalars import JSON
from strawberry_django_aggregates import BigInt

from angee.graphql.actions import ActionResult, action_guard, resolve_action_target
from angee.graphql.capabilities import permissions_field
from angee.graphql.data import hasura_model_resource
from angee.graphql.ids import PublicID
from angee.graphql.node import AngeeNode
from angee.graphql.relations import actor_scoped_public_id
from angee.graphql.subscriptions import changes
from angee.iam.permissions import session_user
from angee.operator_backup.archives import archive_mount
from angee.operator_backup.contracts import BackupResourceGroup, BackupResourceStatus

BackupPlan = apps.get_model("operator_backup", "BackupPlan")
Backup = apps.get_model("operator_backup", "Backup")
strawberry.enum(BackupResourceGroup)
strawberry.enum(BackupResourceStatus)


@strawberry.type
class BackupResourceType:
    """Public discovery evidence, with filesystem paths and credentials omitted."""

    group: BackupResourceGroup
    name: str
    status: BackupResourceStatus
    reason: str
    estimated_bytes: BigInt | None


@strawberry.type
class BackupPreviewType:
    """Point-in-time source estimates, including unknown and excluded resources."""

    version: int
    discovered_at: datetime
    selection: JSON
    resources: list[BackupResourceType]
    estimated_bytes: BigInt
    has_unknown_sizes: bool


@strawberry_django.type(BackupPlan)
class BackupPlanType(AngeeNode):
    """Authored capture intent with its configured Files destination."""

    permissions = permissions_field(("read", "write", "share", "delete", "run"))

    name: auto
    target_stack: auto
    selection: JSON
    destination: auto
    created_at: auto
    updated_at: auto


@strawberry_django.type(Backup)
class BackupType(AngeeNode):
    """A retained archive linked to the file and native workflow owners."""

    plan: strawberry.ID | None = actor_scoped_public_id("plan")
    execution: strawberry.ID | None = actor_scoped_public_id("execution")
    file: strawberry.ID | None = actor_scoped_public_id("file")
    manifest: JSON
    created_at: auto


_PLANS = hasura_model_resource(
    BackupPlanType, model=BackupPlan, name="backup_plans",
    filterable=["id", "name", "target_stack", "created_at", "updated_at"],
    sortable=["name", "created_at", "updated_at"], aggregatable=["id"],
    writable=["name", "selection"],
)
_BACKUPS = hasura_model_resource(
    BackupType, model=Backup, name="backups",
    filterable=["id", "plan", "created_at"], sortable=["created_at"], aggregatable=["id"],
    insert=False, update=False, delete=False,
)


@strawberry.type
class BackupQuery:
    """Discovery delegates all capture facts to the operator."""

    @strawberry.field
    def backup_destination_drive(self) -> strawberry.ID | None:
        """Resolve the singleton Files destination once, with native drive access."""
        mount = archive_mount(required=False)
        actor = current_actor()
        if mount is None or actor is None or not mount.drive.with_actor(actor).has_access("read"):
            return None
        return strawberry.ID(str(mount.drive.sqid))

    @strawberry.field
    def preview_backup_plan(self, id: PublicID) -> BackupPreviewType:
        plan = resolve_action_target(BackupPlan, id, reason="operator_backup.preview")
        data = plan.preview()
        return BackupPreviewType(
            version=1, discovered_at=timezone.now(), selection=plan.snapshot()["selection"],
            resources=[BackupResourceType(
                group=BackupResourceGroup(row["group"]), name=str(row["name"]),
                status=BackupResourceStatus(row["status"]),
                reason=str(row.get("reason") or ""),
                estimated_bytes=BigInt(int(row["estimated_bytes"])) if row.get("estimated_bytes") is not None else None,
            ) for row in data["resources"]],
            estimated_bytes=BigInt(int(data["estimated_bytes"])), has_unknown_sizes=bool(data["has_unknown_sizes"]),
        )


@strawberry.type
class BackupMutation:
    """Execution authority remains on the plan, including non-admin delegation."""

    @strawberry.mutation
    @action_guard("Start backup failed.", camel_case_keys=False)
    def run_backup_plan(self, info: strawberry.Info, id: PublicID, request_key: str) -> ActionResult:
        plan = resolve_action_target(BackupPlan, id, reason="operator_backup.run")
        run = plan.start(session_user(info), request_key=request_key)
        return ActionResult(ok=True, message="Backup started.", id=strawberry.ID(str(run.sqid)))


schemas = {
    "console": {
        "query": [_PLANS.query, _BACKUPS.query, BackupQuery],
        "mutation": [_PLANS.mutation, BackupMutation],
        "subscription": [changes(BackupPlan, field="backupPlanChanged"), changes(Backup, field="backupChanged")],
        "types": [BackupPlanType, BackupType, BackupPreviewType, BackupResourceType, *_PLANS.types, *_BACKUPS.types],
    },
}
