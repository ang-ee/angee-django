"""Capture IO and transactional publication through native workflow steps."""

from __future__ import annotations

import logging
import re
from datetime import timedelta
from pathlib import PurePosixPath
from typing import Any, Literal, cast

from django.apps import apps
from django.core.exceptions import ValidationError
from pydantic import BaseModel, ConfigDict, Field, field_validator
from rebac import system_context

from angee.operator.daemon import OperatorDaemon, OperatorDaemonError
from angee.operator_backup.archives import (
    archive_mount,
    archive_mount_signature,
    validate_archive_destination,
    validate_archive_file,
)
from angee.operator_backup.contracts import CaptureInput
from angee.workflows.context import StepContext
from angee.workflows.steps import Retryable, RetryPolicy, Settlement, Step, StepMode

logger = logging.getLogger(__name__)


class ArchiveReceipt(BaseModel):
    """Authenticated evidence from the operator that verifies and publishes bytes."""

    model_config = ConfigDict(extra="ignore", frozen=True)
    path: str
    size_bytes: int = Field(ge=0)
    sha256: str = Field(pattern=r"^[a-f0-9]{64}$")
    manifest_version: Literal[1]
    manifest: dict[str, Any]

    @field_validator("path")
    @classmethod
    def archive_path(cls, value: str) -> str:
        """Only the operator's canonical archive publication directory is valid."""
        path = PurePosixPath(value)
        if path.parent != PurePosixPath("backups/archives") or not re.fullmatch(r"[A-Za-z0-9_-]+\.zip", path.name):
            raise ValueError("The operator returned an invalid backup archive path.")
        return value

    @property
    def storage_path(self) -> str:
        """The configured archive mount is rooted at backups/archives."""
        return PurePosixPath(self.path).name


class CapturedArchive(ArchiveReceipt):
    """Authenticated operator receipt plus the checked local destination binding."""

    mount_signature: str


class Capture(Step[CaptureInput, CapturedArchive, None]):
    """Dispatch once and index authenticated proof from the archive verification owner."""

    key = "operator_backup_capture"
    label = "Capture stack backup"
    mode = StepMode.IO
    effect_idempotent = True
    retry = RetryPolicy(max_attempts=3, backoff=timedelta(seconds=5), manual=False)

    def run(self, ctx: StepContext) -> Settlement:
        plan = cast(Any, ctx.subject)
        plan.require_access("run", ctx.actor)
        daemon = OperatorDaemon.from_settings()
        mount = archive_mount()
        ctx.heartbeat()
        try:
            root = validate_archive_destination(mount, stack_root=daemon.stack_root())
            ctx.begin_effect()
            operation = daemon.start_backup(request_id=str(ctx.run.sqid),
                                            selection=ctx.input.selection.model_dump(mode="json"))
            operation = daemon.job_run(str(operation["id"]))
        except OperatorDaemonError as error:
            # Raw response text can contain private source paths. Retain only
            # the native classification in server logs and safe workflow text.
            logger.warning("Operator backup refusal: HTTP %s, code=%s, cause=%s",
                           error.status_code, error.code, error.cause)
            status_code = error.status_code
            if status_code == 409 and error.cause == "backup_request_conflict":
                return ctx.fail(
                    "The operator capture identity belongs to a different selection. "
                    "Reprocess this workflow to create a new capture identity."
                )
            if status_code in {401, 403}:
                return ctx.fail(
                    "The operator refused backup access. Ask an administrator to check its server-side "
                    "credentials and backup permissions, then reprocess this workflow."
                )
            if status_code == 404:
                return ctx.fail(
                    "The operator backup endpoint or operation was not found. Check the configured "
                    "operator URL and backup support, then reprocess this workflow."
                )
            if status_code is not None and 400 <= status_code < 500 and status_code not in {408, 409, 425, 429}:
                return ctx.fail(
                    "The operator rejected this backup request. Review the plan's discovered resources "
                    "and selection, then reprocess this workflow."
                )
            raise Retryable(
                "The operator is temporarily unavailable or busy. Automatic retries preserve this capture's "
                "identity; reprocess the workflow if its retry allowance is exhausted."
            ) from None
        if operation["id"] != str(ctx.run.sqid):
            raise ValidationError("The operator returned a different backup operation identity.")
        status = operation.get("status")
        if status in {"pending", "running"}:
            # Only the operator's declared public progress fields enter the run;
            # transport errors, output and source paths remain server-side.
            nodes = [
                {"name": node["name"], "status": node["status"]}
                for node in operation.get("nodes", [])
                if node.get("kind") == "backup" and node.get("name") in {"capture", "verify"}
                and node.get("status") in {"pending", "running", "succeeded", "failed"}
            ]
            current = operation.get("current_step", "")
            return ctx.wait(until=ctx.now + timedelta(seconds=3), state={
                "operation_id": operation["id"], "status": status, "nodes": nodes,
                "current_step": current if current in {"capture", "verify"} else "",
            })
        if status != "succeeded":
            return ctx.fail("The operator capture failed. Reprocess this workflow to start a new capture.")
        receipt = ArchiveReceipt.model_validate(operation.get("artifact"))
        if receipt.manifest.get("version") != 1:
            raise ValidationError("The operator returned an unsupported backup manifest.")
        if receipt.storage_path != f"{ctx.run.sqid}.zip":
            raise ValidationError("The operator returned another capture's archive path.")
        validate_archive_file(root, filename=receipt.storage_path, size_bytes=receipt.size_bytes)
        ctx.note("The operator verified and published the archive; its Files destination was checked.")
        return ctx.done(CapturedArchive(**receipt.model_dump(), mount_signature=archive_mount_signature(mount)))


class PublishedBackup(BaseModel):
    """The durable archive record created by publication."""

    backup_id: str


class Publish(Step[CapturedArchive, PublishedBackup, None]):
    """Publish verified bytes and immutable evidence in the database transaction."""

    key = "operator_backup_publish"
    label = "Publish backup archive"

    def run(self, ctx: StepContext) -> Settlement:
        plan = cast(Any, ctx.subject_for_update())
        plan.require_access("run", ctx.actor)
        receipt = ctx.input
        mount = archive_mount(lock=True)
        if archive_mount_signature(mount) != receipt.mount_signature:
            raise ValidationError(
                "The backup destination changed after capture; restore its configuration before retrying."
            )
        with system_context(reason="operator_backup.index_archive"):
            file = apps.get_model("storage", "File").objects.index_external(
                drive=mount.drive, storage_path=receipt.storage_path, filename=receipt.storage_path,
                content_hash=receipt.sha256, size_bytes=receipt.size_bytes, mime_type="application/zip",
            )
            backup = apps.get_model("operator_backup", "Backup").objects.publish(
                plan=plan, execution=ctx.run, file=file, manifest=receipt.manifest,
            )
        ctx.record(backup, "Completed backup", operation="created")
        ctx.record(file, "Archive file", operation="created")
        return ctx.done(PublishedBackup(backup_id=str(backup.sqid)))
