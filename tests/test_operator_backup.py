"""Manual backup selection, authorization, workflow recovery and publication."""

from __future__ import annotations

import hashlib
import json
from types import SimpleNamespace

import httpx2
import pytest
from django.core.exceptions import PermissionDenied, ValidationError
from rebac import actor_context, system_context

from angee.operator.daemon import OperatorDaemon, OperatorDaemonError
from angee.operator_backup.contracts import BackupResourceGroup, BackupSelection, BackupTarget, CaptureInput
from angee.operator_backup.models import WORKFLOW_KEY
from angee.operator_backup.steps import ArchiveReceipt
from angee.operator_backup.testing.models import Backup, BackupPlan
from angee.workflows.testing.drivers import load_workflow, run_until
from angee.workflows.testing.models import WorkflowRun
from tests.conftest import Backend, Drive, File, Vendor, create_user, make_mount


@pytest.fixture
def backup_env(execution, tmp_path, monkeypatch, settings):
    """Use real storage and workflow owners, replacing only the external daemon."""
    actor, _ = execution
    settings.ANGEE_OPERATOR_BACKUP_MOUNT_DRIVE = "mount-operator-backups"
    root = tmp_path / "backups" / "archives"
    root.mkdir(parents=True)
    with system_context(reason="test backup environment"):
        Vendor.objects.get_or_create(slug="local", defaults={"display_name": "Local"})
        backend = Backend.objects.create(slug="backup-archive", label="Archives", backend_class="local_folder",
                                         backend_config={"root": str(root)})
        drive = Drive.objects.create(slug="mount-operator-backups", name="Backups", backend=backend)
        mount = make_mount("backup-archives", drive=drive, root=root, owner=actor)
        plan = BackupPlan.objects.create(name="Manual backup", owner=actor)
    load_workflow({"nodes": {
        "capture": {"step": "operator_backup_capture", "next": {"done": "publish"}},
        "publish": {"step": "operator_backup_publish", "input": {"from": "capture"}},
    }, "results": [{"from": "publish"}]}, key=WORKFLOW_KEY, actor=actor,
        subject_model="operator_backup.BackupPlan")
    calls = []
    data = b"test operator archive"
    manifest = {"version": 1, "resources": [{"group": "storage", "name": "assets", "status": "included"}]}

    def submit(self, *, request_id, selection):
        calls.append((request_id, selection))
        (root / f"{request_id}.zip").write_bytes(data)
        return {"id": request_id}

    def poll(self, operation_id):
        return {"id": operation_id, "status": "succeeded", "artifact": {
            "path": f"backups/archives/{operation_id}.zip", "size_bytes": len(data),
            "sha256": hashlib.sha256(data).hexdigest(), "manifest_version": 1, "manifest": manifest,
        }}

    native_start_backup = OperatorDaemon.start_backup
    monkeypatch.setattr(OperatorDaemon, "stack_root", lambda self: str(tmp_path))
    monkeypatch.setattr(OperatorDaemon, "start_backup", submit)
    monkeypatch.setattr(OperatorDaemon, "job_run", poll)
    return SimpleNamespace(actor=actor, plan=plan, mount=mount, root=root, calls=calls, manifest=manifest,
                           native_start_backup=native_start_backup)


def test_selection_rejects_unknown_versions_groups_contradictions_and_duplicate_ids():
    for value in ({"version": 2}, {"unowned": {}}, {"databases": {"mode": "none", "names": ["app"]}},
                  {"storage": {"mode": "selected", "names": ["app", "app"]}},
                  {"sources": {"mode": "selected", "names": ["app"], "exclude": ["app"]}}):
        with pytest.raises(ValueError):
            BackupSelection.model_validate(value)
    assert BackupSelection.model_validate({"sources": {"policy": "bundle"}}).sources.policy == "bundle"


def test_owner_and_viewer_cannot_start_or_preview_without_explicit_runner_grant(backup_env, monkeypatch):
    env = backup_env
    owner, outsider = create_user("backup-owner"), create_user("backup-outsider")
    with system_context(reason="test transfer backup ownership"):
        env.plan.owner = owner
        env.plan.save()
    for actor in (owner, outsider):
        with actor_context(actor), pytest.raises(PermissionDenied):
            env.plan.start(actor, request_key="denied")
        with actor_context(actor), pytest.raises(PermissionDenied):
            env.plan.preview()
    env.plan.with_actor(env.actor).grant_record_access("runner", owner)
    with actor_context(owner):
        run = env.plan.start(owner, request_key="delegated")
    assert run.run_as_id == owner.pk


def test_request_replay_preserves_input_and_single_active_admission(backup_env):
    env = backup_env
    run = env.plan.start(env.actor, request_key="same")
    original = run.input
    with system_context(reason="test plan edit"):
        env.plan.selection = {"storage": {"mode": "none"}}
        env.plan.save()
    assert env.plan.start(env.actor, request_key="same").pk == run.pk
    assert WorkflowRun.system_queryset().get(pk=run.pk).input == original
    with pytest.raises(ValidationError, match="active"):
        env.plan.start(env.actor, request_key="another")


def test_completed_backup_links_verified_file_and_frozen_manifest(backup_env):
    env = backup_env
    run = env.plan.start(env.actor, request_key="complete")
    run_until(run)
    assert run.status == "succeeded", run.error
    backup = Backup.system_queryset().get(execution=run)
    assert backup.plan_id == env.plan.pk
    assert backup.manifest == env.manifest
    assert backup.file.content_hash == hashlib.sha256(b"test operator archive").hexdigest()
    assert backup.file.drive_id == env.mount.drive_id
    assert env.calls[0][0] == run.sqid
    assert Backup.objects.publish(plan=env.plan, execution=run, file=backup.file, manifest=env.manifest).pk == backup.pk
    with pytest.raises(ValidationError, match="cannot be edited"):
        backup.save()
    with pytest.raises(ValidationError, match="cannot be edited"):
        Backup.system_queryset().filter(pk=backup.pk).update(manifest={"version": 2})


def test_failed_operator_never_publishes_backup(backup_env, monkeypatch):
    env = backup_env
    monkeypatch.setattr(OperatorDaemon, "job_run", lambda self, id: {"id": id, "status": "failed"})
    run = env.plan.start(env.actor, request_key="failed")
    run_until(run)
    assert run.status == "failed"
    assert not Backup.system_queryset().exists()
    assert not File.system_queryset().filter(drive=env.mount.drive).exists()


def test_receipt_refuses_archive_path_escape():
    for path in ("../../secrets.zip", "backups/archives/../escape.zip", "/backups/archives/test.zip"):
        with pytest.raises(ValueError):
            ArchiveReceipt.model_validate({"path": path, "size_bytes": 0, "sha256": "a" * 64,
                                           "manifest_version": 1, "manifest": {"version": 1}})


def test_capture_checks_size_before_storage_publication(backup_env, monkeypatch):
    env = backup_env
    original = OperatorDaemon.job_run

    def wrong_size(self, operation_id):
        result = original(self, operation_id)
        result["artifact"]["size_bytes"] += 1
        return result

    monkeypatch.setattr(OperatorDaemon, "job_run", wrong_size)
    run = env.plan.start(env.actor, request_key="wrong-size")
    run_until(run)
    assert run.status == "failed"
    assert not Backup.system_queryset().exists()
    assert not File.system_queryset().filter(drive=env.mount.drive).exists()


def test_failed_publication_rolls_back_file_and_completed_record(backup_env, monkeypatch):
    env = backup_env

    def refuse(self, **kwargs):
        raise ValidationError("Storage publication refused.")

    monkeypatch.setattr(type(Backup.objects), "publish", refuse)
    run = env.plan.start(env.actor, request_key="publish-failure")
    run_until(run)
    assert run.status == "failed"
    assert not Backup.system_queryset().exists()
    assert not File.system_queryset().filter(drive=env.mount.drive).exists()
    assert list(env.root.glob("*.zip")), "Durable operator archives survive database failure."


def test_delegated_runner_reads_completed_backup_and_download_file_without_drive_access(backup_env):
    env = backup_env
    runner, outsider = create_user("delegated-runner"), create_user("backup-reader-outsider")
    env.plan.with_actor(env.actor).grant_record_access("runner", runner)
    run = env.plan.start(runner, request_key="runner-completion")
    run_until(run)
    assert run.status == "succeeded", run.error
    backup = Backup.system_queryset().get(execution=run)
    assert not env.mount.drive.with_actor(runner).has_access("read")
    assert backup.with_actor(runner).has_access("read")
    assert backup.file.with_actor(runner).has_access("read")
    assert not backup.file.with_actor(outsider).has_access("read")
    assert not backup.with_actor(outsider).has_access("read")


def test_console_schema_builds_and_preview_sanitizes_operator_paths(backup_env, monkeypatch):
    from angee.operator_backup import schema as backup_schema
    from tests.conftest import addon_schema, execute_schema, result_data

    env = backup_env
    monkeypatch.setattr(OperatorDaemon, "backup_preview", lambda self, **kwargs: {
        "resources": [{"group": "storage", "name": "assets", "status": "included",
                       "path": "/private/secret-folder", "estimated_bytes": 5_000_000_000}],
        "estimated_bytes": 5_000_000_000, "has_unknown_sizes": False,
    })
    schema = addon_schema(backup_schema.schemas, "console")
    data = result_data(execute_schema(schema, """query($id: ID!) {
        preview_backup_plan(id: $id) { selection discovered_at estimated_bytes
            resources { group name status estimated_bytes reason } }
    }""", {"id": str(env.plan.sqid)}, user=env.actor))
    preview = data["preview_backup_plan"]
    assert preview["estimated_bytes"] == "5000000000"
    assert "/private/secret-folder" not in str(data)
    assert preview["selection"]["version"] == 1
    assert preview["resources"][0]["group"] == "STORAGE"
    assert preview["resources"][0]["status"] == "INCLUDED"
    assert preview["discovered_at"]
    target = result_data(execute_schema(schema, "{ backup_plans { target_stack } }", user=env.actor))
    assert target["backup_plans"][0]["target_stack"] == "LOCAL"
    assert env.plan.snapshot()["target_stack"] == BackupTarget.LOCAL.value


def test_poll_wait_reuses_operation_identity_and_snapshot_after_plan_edit(backup_env, monkeypatch):
    from datetime import timedelta

    from django.db.models.functions import Now

    from angee.workflows.runner import runner
    from angee.workflows.testing.models import StepRun

    env = backup_env
    original_poll = OperatorDaemon.job_run
    pending = True

    def poll(self, operation_id):
        if pending:
            return {"id": operation_id, "status": "running"}
        return original_poll(self, operation_id)

    monkeypatch.setattr(OperatorDaemon, "job_run", poll)
    run = env.plan.start(env.actor, request_key="polled")
    run_until(run)
    step = StepRun.system_queryset().get(run=run, node_key="capture")
    assert step.status == "waiting"
    assert step.state["operation_id"] == run.sqid
    with system_context(reason="test due backup poll and changed plan"):
        env.plan.selection = {"storage": {"mode": "none"}}
        env.plan.save()
        StepRun.objects.filter(pk=step.pk).update(wake_at=Now() - timedelta(seconds=1))
    pending = False
    runner.tick()
    run_until(run)
    assert run.status == "succeeded", run.error
    assert len(env.calls) == 2
    assert env.calls[0] == env.calls[1]
    assert Backup.system_queryset().filter(execution=run).count() == 1


def test_terminal_capture_requires_reprocess_with_new_identity_and_frozen_snapshot(backup_env, monkeypatch):
    from angee.workflows.testing.models import StepRun

    env = backup_env
    original_poll = OperatorDaemon.job_run
    monkeypatch.setattr(OperatorDaemon, "job_run", lambda self, id: {"id": id, "status": "failed"})
    run = env.plan.start(env.actor, request_key="retried")
    run_until(run)
    assert run.status == "failed"
    with system_context(reason="test plan changes after failed backup"):
        env.plan.selection = {"databases": {"mode": "none"}}
        env.plan.save()
    monkeypatch.setattr(OperatorDaemon, "job_run", original_poll)
    step = StepRun.system_queryset().get(run=run, node_key="capture")
    assert not step.can_retry(env.actor)
    with pytest.raises(ValidationError, match="cannot be retried"):
        StepRun.objects.retry_step(step, actor=env.actor)
    replacement = WorkflowRun.objects.reprocess(run, actor=env.actor)
    run_until(replacement)
    assert replacement.status == "succeeded", replacement.error
    assert replacement.reprocess_of_id == run.pk
    assert env.calls[0][0] != env.calls[1][0]
    assert env.calls[0][1] == env.calls[1][1]


def test_capture_uses_operator_verification_without_reopening_archive_bytes(backup_env, monkeypatch):
    from angee.storage_integrate.backends import LocalFolderBackend

    def refuse_read(*args, **kwargs):
        raise AssertionError("Django must not repeat the operator's full archive verification.")

    monkeypatch.setattr(LocalFolderBackend, "open", refuse_read)
    run = backup_env.plan.start(backup_env.actor, request_key="operator-proof")
    run_until(run)
    assert run.status == "succeeded", run.error


@pytest.mark.parametrize("drift", ["operator_root", "mount_root", "drive_prefix", "backend_root"])
def test_capture_refuses_destination_identity_drift_before_dispatch(backup_env, monkeypatch, tmp_path, drift):
    env = backup_env
    other = tmp_path / "other" / "backups" / "archives"
    other.mkdir(parents=True)
    with system_context(reason="test archive identity drift"):
        if drift == "operator_root":
            monkeypatch.setattr(OperatorDaemon, "stack_root", lambda self: str(tmp_path / "other"))
        elif drift == "mount_root":
            env.mount.config = {"root": str(other)}
            env.mount.save()
        elif drift == "drive_prefix":
            env.mount.drive.prefix = "wrong-prefix"
            env.mount.drive.save()
        else:
            backend = env.mount.drive.backend
            backend.backend_config = {"root": str(other)}
            backend.save()
    run = env.plan.start(env.actor, request_key="wrong-destination")
    run_until(run)
    assert run.status == "failed"
    assert not env.calls
    assert not Backup.system_queryset().exists()


def test_capture_refuses_symlink_archive_from_operator_receipt(backup_env, monkeypatch):
    env = backup_env
    original_poll = OperatorDaemon.job_run

    def replace_with_link(self, operation_id):
        result = original_poll(self, operation_id)
        archive = env.root / f"{operation_id}.zip"
        target = env.root / "other.zip"
        archive.rename(target)
        archive.symlink_to(target)
        return result

    monkeypatch.setattr(OperatorDaemon, "job_run", replace_with_link)
    run = env.plan.start(env.actor, request_key="symlink")
    run_until(run)
    assert run.status == "failed"
    assert not Backup.system_queryset().exists()


def test_publication_refuses_mount_configuration_changed_after_capture(backup_env):
    env = backup_env
    run = env.plan.start(env.actor, request_key="changed-after-capture")
    run_until(run, node="publish")
    with system_context(reason="test archive publication drift"):
        env.mount.drive.prefix = "changed"
        env.mount.drive.save()
    run_until(run)
    assert run.status == "failed"
    assert not Backup.system_queryset().exists()
    assert not File.system_queryset().filter(drive=env.mount.drive).exists()


def test_destination_singleton_does_not_scale_mount_queries_with_plan_count(backup_env, monkeypatch):
    from angee.operator_backup import schema as backup_schema
    from tests.conftest import addon_schema, execute_schema, result_data

    env = backup_env
    with system_context(reason="test many plans with one destination"):
        for index in range(3):
            BackupPlan.objects.create(name=f"Additional {index}")
    mount_reads = []
    original = backup_schema.archive_mount

    def counted(**kwargs):
        mount_reads.append(True)
        return original(**kwargs)

    monkeypatch.setattr(backup_schema, "archive_mount", counted)
    schema = addon_schema(backup_schema.schemas, "console")
    data = result_data(execute_schema(schema, "{ backup_plans { id name } backup_destination_drive }", user=env.actor))
    assert len(data["backup_plans"]) == 4
    assert len(mount_reads) == 1
    outsider = create_user("destination-outsider")
    data = result_data(execute_schema(schema, "{ backup_destination_drive }", user=outsider))
    assert data["backup_destination_drive"] is None



@pytest.mark.parametrize(("status", "cause", "transient"), [
    (400, "", False), (401, "", False), (403, "", False), (404, "", False), (422, "", False),
    (409, "", True), (409, "backup_request_conflict", False), (503, "", True),
])
def test_operator_http_refusal_classification_retains_only_safe_workflow_guidance(
    backup_env, monkeypatch, status, cause, transient,
):
    from angee.workflows.testing.models import StepAttempt, StepRun

    env = backup_env

    def refuse(self, **kwargs):
        raise OperatorDaemonError.from_response(
            "POST", "http://operator/backups", status,
            json.dumps({"error": "source /private/sensitive/path token=server-secret",
                        "code": "CONFLICT" if status == 409 else "REFUSED", "cause": cause}).encode(),
        )

    monkeypatch.setattr(OperatorDaemon, "start_backup", refuse)
    run = env.plan.start(env.actor, request_key=f"http-{status}")
    run_until(run)
    step = StepRun.system_queryset().get(run=run, node_key="capture")
    assert step.status == ("waiting" if transient else "failed")
    assert run.status == ("waiting" if transient else "failed")
    assert step.attempt == 1
    if not transient:
        assert "reprocess" in str(step.output).lower()
        assert "availability" not in str(step.output).lower()
    attempts = StepAttempt.system_queryset().filter(step_run=step).values("error", "stacktrace")
    evidence = str(step.output) + str(list(attempts))
    assert "/private/sensitive/path" not in evidence
    assert "server-secret" not in evidence
    assert not Backup.system_queryset().exists()



def test_selection_and_preview_share_the_canonical_resource_group_vocabulary():
    assert {group.value for group in BackupResourceGroup} == set(BackupSelection.model_fields) - {"version"}
    snapshot = CaptureInput(selection=BackupSelection(), target_stack=BackupTarget.LOCAL).model_dump(mode="json")
    assert snapshot["target_stack"] == "local"


def test_target_state_field_deconstructs_with_canonical_choices():
    from angee.fields import StateField

    field = BackupPlan._meta.get_field("target_stack")
    assert isinstance(field, StateField)
    assert field.choices_enum is BackupTarget
    _name, path, args, kwargs = field.deconstruct()
    restored = StateField(*args, **kwargs)
    assert path == "angee.fields.StateField"
    assert list(restored.flatchoices) == list(BackupTarget.choices)
    assert restored.default == BackupTarget.LOCAL



@pytest.mark.parametrize("error_class", [httpx2.ReadTimeout, httpx2.ReadError, httpx2.WriteError])
def test_post_send_transport_failure_retries_stable_capture_without_private_diagnostics(
    backup_env, monkeypatch, settings, error_class,
):
    from datetime import timedelta

    from django.db.models.functions import Now

    from angee.integrate.http import HttpClient
    from angee.workflows.runner import runner
    from angee.workflows.testing.models import StepAttempt, StepRun

    env = backup_env
    settings.ANGEE_OPERATOR_URL = "http://operator"
    settings.ANGEE_OPERATOR_TOKEN = "server-secret"
    requests = []

    def post(self, method, url, **kwargs):
        assert method == "POST" and url.endswith("/backups")
        payload = json.loads(kwargs["body"])
        requests.append(payload)
        if len(requests) == 1:
            raise error_class("request accepted; private /source/path token=server-secret")
        (env.root / f"{payload['request_id']}.zip").write_bytes(b"test operator archive")
        return httpx2.Response(202, json={"id": payload["request_id"]})

    monkeypatch.setattr(OperatorDaemon, "start_backup", env.native_start_backup)
    monkeypatch.setattr(HttpClient, "request", post)
    run = env.plan.start(env.actor, request_key="uncertain-transport")
    run_until(run)
    step = StepRun.system_queryset().get(run=run, node_key="capture")
    assert step.status == "waiting" and step.waiting_kind == "time" and step.attempt == 1
    attempts = StepAttempt.system_queryset().filter(step_run=step).values("error", "stacktrace")
    evidence = str(step.output) + str(list(attempts))
    assert "/source/path" not in evidence and "server-secret" not in evidence
    with system_context(reason="test resume uncertain capture transport"):
        StepRun.objects.filter(pk=step.pk).update(wake_at=Now() - timedelta(seconds=1))
    assert runner.wake() == 1
    run_until(run)
    assert run.status == "succeeded", run.error
    assert requests[0] == requests[1]
    assert requests[0]["request_id"] == run.sqid
    assert Backup.system_queryset().filter(execution=run).count() == 1
