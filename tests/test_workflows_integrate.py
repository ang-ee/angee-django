"""Tests for the vendor-free workflows-integrate archive bridge."""

from __future__ import annotations

import io
import stat
import zipfile
from collections.abc import Iterator
from pathlib import Path, PurePosixPath
from typing import Any, ClassVar

import pytest
from django.contrib.auth import get_user_model
from django.test import override_settings
from rebac import system_context

from angee.workflows import engine
from angee.workflows import models as workflow_models
from angee.workflows_integrate import archives
from angee.workflows_integrate.autoconfig import SETTINGS as WORKFLOWS_INTEGRATE_SETTINGS
from angee.workflows_integrate.steps import ArchiveExecutionReporter, ArchiveExtractor
from tests.conftest import STORAGE_TEST_MODELS, Backend, Drive, File
from tests.workflows import (
    WORKFLOW_RUNTIME_MODELS,
    Decision,
    StepRun,
    advance_once,
    execute_started,
    run_to_terminal,
    step_run_for,
    workflow_table_setup,
    workflow_with_steps,
)

User = get_user_model()
pytest_plugins = ("tests.workflows",)

_FIXTURE_ARCHIVE = b"fixture archive payload"

_PAIRED_EXTRACTORS = {
    "aux_archive": "tests.test_workflows_integrate.AuxArchiveExtractor",
    "fixture_archive": "tests.test_workflows_integrate.FixtureArchiveExtractor",
}
_HETEROGENEOUS_EXTRACTORS = {
    "fixture_archive": "tests.test_workflows_integrate.FixtureArchiveExtractor",
    "hetero_archive": "tests.test_workflows_integrate.HeteroArchiveExtractor",
}


def test_archive_member_names_reject_root_traversal() -> None:
    """The bridge rejects archive paths that escape their extraction root."""

    with pytest.raises(archives.ArchiveError, match="escapes its archive root"):
        archives.safe_member_name("../outside.txt")


def test_archive_entries_reject_duplicate_normalized_members() -> None:
    """The bridge rejects ambiguous ZIPs instead of choosing one duplicate."""

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive, pytest.warns(UserWarning):
        archive.writestr("bundle/item.txt", "first")
        archive.writestr("bundle/item.txt", "second")
    buffer.seek(0)

    with zipfile.ZipFile(buffer) as archive:
        with pytest.raises(archives.ArchiveError, match="repeats member"):
            archives.archive_entries(archive)


def test_archive_extraction_rejects_symbolic_links(tmp_path: Path) -> None:
    """The bridge never follows or materializes a ZIP symbolic link."""

    buffer = io.BytesIO()
    link = zipfile.ZipInfo("bundle/link")
    link.external_attr = (stat.S_IFLNK | 0o777) << 16
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr(link, "target.txt")
    buffer.seek(0)

    with zipfile.ZipFile(buffer) as archive:
        entries = archives.archive_entries(archive)
        with pytest.raises(archives.ArchiveError, match="symbolic link"):
            archives.extract_archive(archive, tmp_path, entries=entries)


def test_bounded_reader_rejects_reads_beyond_its_budget() -> None:
    """Every archive probe fails closed before exceeding its declared budget."""

    reader = archives.BoundedReader(io.BytesIO(b"four"), limit=3)

    with pytest.raises(archives.ArchiveError, match="bounded read budget"):
        reader.read(4)


def test_archive_subtree_selection_keeps_only_the_declared_parent() -> None:
    """Subtree selection cannot stage sibling exports from the same ZIP."""

    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("chosen/result.json", "{}")
        archive.writestr("chosen/media/photo.jpg", b"photo")
        archive.writestr("sibling/secret.txt", "secret")
    buffer.seek(0)

    with zipfile.ZipFile(buffer) as archive:
        selected = archives.subtree_entries(
            archives.archive_entries(archive),
            PurePosixPath("chosen"),
        )

    assert set(selected) == {
        "chosen/media/photo.jpg",
        "chosen/result.json",
    }


def test_stage_subtree_rejects_declared_content_above_the_shared_cap(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The staging owner enforces one cap for every vendor extractor."""

    monkeypatch.setattr(archives, "EXTRACT_DECLARED_LIMIT", 3)
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        archive.writestr("bundle/result.json", "four")
    buffer.seek(0)

    with zipfile.ZipFile(buffer) as archive:
        with (
            pytest.raises(archives.ArchiveError, match="supported extraction size"),
            archives.stage_subtree(archive, PurePosixPath("bundle")),
        ):
            pass


class FixtureArchiveIngest:
    """Fixture target-domain ingest surface with idempotent file/target identity."""

    landed: ClassVar[dict[tuple[str, str], dict[str, Any]]] = {}

    @classmethod
    def ingest(
        cls,
        file: Any,
        target_pk: str,
        reporter: ArchiveExecutionReporter,
    ) -> dict[str, Any]:
        """Land the fixture archive once for a file hash and target id."""

        reporter.heartbeat()
        identity = (str(file.content_hash), target_pk)
        with file.open_stream() as stream:
            content = stream.read()
        cls.landed.setdefault(
            identity,
            {
                "content": content.decode("utf-8"),
                "target": target_pk,
            },
        )
        return {"landed": 1, "target": target_pk}


class FixtureArchiveExtractor(ArchiveExtractor):
    """Test extractor proving the settings registry and target ingest boundary."""

    key = "fixture_archive"
    label = "Fixture archive"
    target_resource = "storage.Drive"

    def recognizes(self, file: Any) -> bool:
        """Recognize the fixture payload exactly."""

        with file.open_stream() as stream:
            return stream.read() == _FIXTURE_ARCHIVE

    def execute(
        self,
        file: Any,
        target_pk: str,
        reporter: ArchiveExecutionReporter,
    ) -> dict[str, Any]:
        """Delegate landing to the fixture target-domain ingest owner."""

        return FixtureArchiveIngest.ingest(file, target_pk, reporter)


class AuxArchiveExtractor(FixtureArchiveExtractor):
    """Second registered extractor whose ingest always fails (partial-failure case)."""

    key = "aux_archive"
    label = "Aux archive"

    def execute(
        self,
        file: Any,
        target_pk: str,
        reporter: ArchiveExecutionReporter,
    ) -> dict[str, Any]:
        """Fail deterministically so map partial-failure accounting is observable."""

        del file, target_pk, reporter
        raise RuntimeError("aux archive ingest exploded")


class HeteroArchiveExtractor(FixtureArchiveExtractor):
    """Extractor declaring a different target resource (unsupported v1 mix)."""

    key = "hetero_archive"
    label = "Hetero archive"
    target_resource = "iam.User"

    def execute(
        self,
        file: Any,
        target_pk: str,
        reporter: ArchiveExecutionReporter,
    ) -> dict[str, Any]:
        """Never runs — the heterogeneous gate routes down the failed edge."""

        raise AssertionError("heterogeneous mapping must never execute")


@pytest.fixture()
def workflows_integrate_tables(transactional_db: Any) -> Iterator[None]:
    """Create workflow and storage tables with one local archive file."""

    del transactional_db
    FixtureArchiveIngest.landed.clear()
    models = STORAGE_TEST_MODELS + WORKFLOW_RUNTIME_MODELS
    with workflow_table_setup(models):
        yield
    FixtureArchiveIngest.landed.clear()


def test_probe_gate_map_execute_archive_end_to_end(
    workflows_integrate_tables: None,
    no_workflow_queue: None,
    tmp_path: Path,
) -> None:
    """Probe, form gate, and stock map units land through the extractor ingest owner."""

    del workflows_integrate_tables, no_workflow_queue
    operator = User.objects.create_user(username="archive-operator")
    file, drive = _archive_storage(tmp_path, operator=operator, content=_FIXTURE_ARCHIVE)
    workflow = _archive_workflow()

    run = engine.start(workflow, subject=file, actor=operator)
    advance_once(run)
    execute_started(run)
    advance_once(run)
    execute_started(run)

    probe = step_run_for(run, "probe")
    assert probe.output == {
        "proposals": [
            {
                "extractor": "fixture_archive",
                "label": "Fixture archive",
                "target_resource": "storage.Drive",
            }
        ]
    }
    assert probe.outcome == "recognized"

    with system_context(reason="test workflows integrate decision"):
        decision = Decision.objects.select_related("step_run").get(step_run__run=run)
    expected_schema = {
        "type": "object",
        "required": ["mappings"],
        "properties": {
            "mappings": {
                "type": "array",
                "widget": "rows",
                "label": "Archive mappings",
                "items": {
                    "type": "object",
                    "required": ["extractor", "label", "target"],
                    "properties": {
                        "extractor": {
                            "type": "string",
                            "label": "Extractor key",
                            "readOnly": True,
                        },
                        "label": {
                            "type": "string",
                            "label": "Archive type",
                            "readOnly": True,
                        },
                        "target": {
                            "type": "string",
                            "label": "Target",
                            "relation": {
                                "resource": "storage.Drive",
                                "create": {"resource": "storage.Drive"},
                            },
                        },
                    },
                },
            }
        },
    }
    assert decision.form_schema == expected_schema
    assert decision.payload == {
        "mappings": [
            {
                "extractor": "fixture_archive",
                "label": "Fixture archive",
                "target": "",
            }
        ]
    }

    attempted = engine.decide(
        decision,
        "complete",
        payload=_resolution(target=str(drive.sqid)),
        actor=operator,
    )
    assert attempted.validation_error is None

    run_to_terminal(run)
    run.refresh_from_db()
    assert run.status == workflow_models.RunStatus.SUCCEEDED
    prepare = step_run_for(run, "prepare")
    assert prepare.output == [{"extractor": "fixture_archive", "target": str(drive.sqid)}]
    map_row = step_run_for(run, "map")
    assert map_row.output == {
        "total": 1,
        "successes": 1,
        "failures": 0,
        "results": [
            {
                "map_index": 0,
                "status": "succeeded",
                "outcome": "completed",
                "output": {
                    "extractor": "fixture_archive",
                    "target": str(drive.sqid),
                    "result": {"landed": 1, "target": str(drive.sqid)},
                },
                "error": "",
            }
        ],
    }
    with system_context(reason="test workflows integrate unit"):
        unit = StepRun.objects.get(run=run, step__key="execute_unit", map_index=0)
    assert unit.output == map_row.output["results"][0]["output"]
    assert FixtureArchiveIngest.landed == {
        (file.content_hash, str(drive.sqid)): {
            "content": _FIXTURE_ARCHIVE.decode("utf-8"),
            "target": str(drive.sqid),
        }
    }


def test_decide_rejects_relation_targets_the_actor_cannot_write(
    workflows_integrate_tables: None,
    no_workflow_queue: None,
    tmp_path: Path,
) -> None:
    """Submitted mapping targets are re-checked against the resolving actor's access.

    Unknown ids and rows the actor holds no ``write`` on share one field-keyed
    rejection (a wrong-model id is the same lookup-miss path), the decision
    re-opens for another attempt, and nothing ingests until a writable target
    resolves.
    """

    del workflows_integrate_tables, no_workflow_queue
    operator = User.objects.create_user(username="archive-decider")
    outsider = User.objects.create_user(username="archive-outsider")
    file, own_drive = _archive_storage(tmp_path, operator=operator, content=_FIXTURE_ARCHIVE)
    _, foreign_drive = _archive_storage(tmp_path, operator=outsider, content=b"foreign payload")

    run = engine.start(_archive_workflow(), subject=file, actor=operator)
    advance_once(run)
    execute_started(run)
    advance_once(run)
    execute_started(run)
    with system_context(reason="test workflows integrate rejection"):
        decision = Decision.objects.select_related("step_run").get(step_run__run=run)

    unknown = engine.decide(decision, "complete", payload=_resolution(target="does-not-exist"), actor=operator)
    assert unknown.validation_error is not None
    assert set(unknown.validation_error.message_dict) == {"mappings.0.target"}

    foreign = engine.decide(
        decision,
        "complete",
        payload=_resolution(target=str(foreign_drive.sqid)),
        actor=operator,
    )
    assert foreign.validation_error is not None
    assert set(foreign.validation_error.message_dict) == {"mappings.0.target"}

    with system_context(reason="test workflows integrate rejection state"):
        decision.refresh_from_db()
    assert decision.verdict == workflow_models.Verdict.PENDING
    assert decision.attempts == 2
    assert FixtureArchiveIngest.landed == {}

    accepted = engine.decide(
        decision,
        "complete",
        payload=_resolution(target=str(own_drive.sqid)),
        actor=operator,
    )
    assert accepted.validation_error is None
    run_to_terminal(run)
    run.refresh_from_db()
    assert run.status == workflow_models.RunStatus.SUCCEEDED
    assert set(FixtureArchiveIngest.landed) == {(file.content_hash, str(own_drive.sqid))}


def test_prepare_rejects_swapped_extractors_and_blank_targets(
    workflows_integrate_tables: None,
    no_workflow_queue: None,
    tmp_path: Path,
) -> None:
    """Prepare fails a resolution that swaps a proposed extractor or blanks a target."""

    del workflows_integrate_tables, no_workflow_queue
    operator = User.objects.create_user(username="archive-preparer")
    file, drive = _archive_storage(tmp_path, operator=operator, content=_FIXTURE_ARCHIVE)

    swapped = _run_to_decision(file, actor=operator)
    attempted = engine.decide(
        swapped,
        "complete",
        payload={
            "mappings": [
                {"extractor": "aux_archive", "label": "Fixture archive", "target": str(drive.sqid)}
            ]
        },
        actor=operator,
    )
    assert attempted.validation_error is None
    swapped_run = swapped.step_run.run
    run_to_terminal(swapped_run)
    prepare = step_run_for(swapped_run, "prepare")
    assert prepare.status == workflow_models.StepRunStatus.FAILED
    assert "changed a proposed extractor" in prepare.error

    blank = _run_to_decision(file, actor=operator)
    attempted = engine.decide(blank, "complete", payload=_resolution(target=""), actor=operator)
    assert attempted.validation_error is None
    blank_run = blank.step_run.run
    run_to_terminal(blank_run)
    prepare = step_run_for(blank_run, "prepare")
    assert prepare.status == workflow_models.StepRunStatus.FAILED
    assert "requires a target" in prepare.error
    assert FixtureArchiveIngest.landed == {}


@override_settings(ANGEE_WORKFLOW_ARCHIVE_EXTRACTOR_CLASSES=_PAIRED_EXTRACTORS)
def test_probe_orders_proposals_by_stable_key(
    workflows_integrate_tables: None,
    no_workflow_queue: None,
    tmp_path: Path,
) -> None:
    """Two recognizing extractors produce deterministically key-ordered proposals."""

    del workflows_integrate_tables, no_workflow_queue
    operator = User.objects.create_user(username="archive-order")
    file, _ = _archive_storage(tmp_path, operator=operator, content=_FIXTURE_ARCHIVE)
    run = engine.start(_archive_workflow(), subject=file, actor=operator)
    advance_once(run)
    execute_started(run)

    probe = step_run_for(run, "probe")
    assert [proposal["extractor"] for proposal in probe.output["proposals"]] == [
        "aux_archive",
        "fixture_archive",
    ]


@override_settings(ANGEE_WORKFLOW_ARCHIVE_EXTRACTOR_CLASSES=_PAIRED_EXTRACTORS)
def test_map_partial_failure_lands_only_successful_units(
    workflows_integrate_tables: None,
    no_workflow_queue: None,
    tmp_path: Path,
) -> None:
    """One failing unit follows the engine's map partial-failure accounting."""

    del workflows_integrate_tables, no_workflow_queue
    operator = User.objects.create_user(username="archive-partial")
    file, drive = _archive_storage(tmp_path, operator=operator, content=_FIXTURE_ARCHIVE)
    decision = _run_to_decision(file, actor=operator)
    target = str(drive.sqid)
    attempted = engine.decide(
        decision,
        "complete",
        payload={
            "mappings": [
                {"extractor": "aux_archive", "label": "Aux archive", "target": target},
                {"extractor": "fixture_archive", "label": "Fixture archive", "target": target},
            ]
        },
        actor=operator,
    )
    assert attempted.validation_error is None

    run = decision.step_run.run
    run_to_terminal(run)
    run.refresh_from_db()
    # Map policy failure routes a "failed" outcome (engine convention), it does
    # not fail the run; with no failed edge the downstream steps are skipped.
    assert run.status == workflow_models.RunStatus.SUCCEEDED
    map_row = step_run_for(run, "map")
    assert map_row.outcome == "failed"
    assert map_row.output["total"] == 2
    assert map_row.output["successes"] == 1
    assert map_row.output["failures"] == 1
    failed_results = [result for result in map_row.output["results"] if result["status"] == "failed"]
    assert len(failed_results) == 1
    assert "aux archive ingest exploded" in failed_results[0]["error"]
    assert set(FixtureArchiveIngest.landed) == {(file.content_hash, target)}


@override_settings(ANGEE_WORKFLOW_ARCHIVE_EXTRACTOR_CLASSES=_HETEROGENEOUS_EXTRACTORS)
def test_gate_routes_heterogeneous_targets_down_the_failed_edge(
    workflows_integrate_tables: None,
    no_workflow_queue: None,
    tmp_path: Path,
) -> None:
    """Mixed target resources produce a clean routable failure, not a stacktrace."""

    del workflows_integrate_tables, no_workflow_queue
    operator = User.objects.create_user(username="archive-hetero")
    file, _ = _archive_storage(tmp_path, operator=operator, content=_FIXTURE_ARCHIVE)
    run = engine.start(_archive_workflow(), subject=file, actor=operator)
    advance_once(run)
    execute_started(run)
    advance_once(run)
    execute_started(run)
    run_to_terminal(run)

    gate = step_run_for(run, "gate")
    assert gate.status == workflow_models.StepRunStatus.SUCCEEDED
    assert gate.outcome == "failed"
    assert gate.output["target_resources"] == ["iam.User", "storage.Drive"]
    assert "one shared target resource" in gate.output["unsupported"]
    prepare = step_run_for(run, "prepare")
    assert prepare.status == workflow_models.StepRunStatus.SKIPPED


def test_execute_is_idempotent_per_file_and_target(
    workflows_integrate_tables: None,
    tmp_path: Path,
) -> None:
    """Re-running one unit lands exactly once through the target-owned identity."""

    del workflows_integrate_tables
    operator = User.objects.create_user(username="archive-idempotent")
    file, drive = _archive_storage(tmp_path, operator=operator, content=_FIXTURE_ARCHIVE)
    reporter = ArchiveExecutionReporter(step=_HeartbeatStub(), step_run=None)  # type: ignore[arg-type]
    extractor = FixtureArchiveExtractor()

    first = extractor.execute(file, str(drive.sqid), reporter)
    second = extractor.execute(file, str(drive.sqid), reporter)

    assert first == second == {"landed": 1, "target": str(drive.sqid)}
    assert len(FixtureArchiveIngest.landed) == 1


def test_probe_no_match_uses_failed_outcome_and_skips_the_gate(
    workflows_integrate_tables: None,
    no_workflow_queue: None,
    tmp_path: Path,
) -> None:
    """No recognized extractor uses the engine's routable failure/skip convention."""

    del workflows_integrate_tables, no_workflow_queue
    operator = User.objects.create_user(username="archive-no-match")
    file, _ = _archive_storage(tmp_path, operator=operator, content=b"unknown archive")
    run = engine.start(_archive_workflow(), subject=file, actor=operator)

    advance_once(run)
    execute_started(run)
    advance_once(run)

    probe = step_run_for(run, "probe")
    gate = step_run_for(run, "gate")
    assert probe.status == workflow_models.StepRunStatus.SUCCEEDED
    assert probe.outcome == "failed"
    assert probe.output == {"proposals": []}
    assert gate.status == workflow_models.StepRunStatus.SKIPPED


def test_autoconfig_contributes_archive_registry_and_workflow_steps() -> None:
    """The bridge composes row-less extractors and step classes through settings."""

    assert WORKFLOWS_INTEGRATE_SETTINGS == {
        "ANGEE_WORKFLOW_ARCHIVE_EXTRACTOR_CLASSES": {},
        "ANGEE_WORKFLOW_STEP_CLASSES.archive_probe": (
            "angee.workflows_integrate.steps.ArchiveProbeStepImpl"
        ),
        "ANGEE_WORKFLOW_STEP_CLASSES.archive_gate": (
            "angee.workflows_integrate.steps.ArchiveGateStepImpl"
        ),
        "ANGEE_WORKFLOW_STEP_CLASSES.archive_execute": (
            "angee.workflows_integrate.steps.ArchiveExecuteStepImpl"
        ),
    }


class _HeartbeatStub:
    """Minimal heartbeat owner for direct extractor execution in tests."""

    def heartbeat(self, step_run: Any, *, at: Any = None) -> None:
        del step_run, at


def _resolution(*, target: str) -> dict[str, Any]:
    """Return the single-row fixture mapping resolution for ``target``."""

    return {
        "mappings": [
            {
                "extractor": "fixture_archive",
                "label": "Fixture archive",
                "target": target,
            }
        ]
    }


def _run_to_decision(file: Any, *, actor: Any) -> Any:
    """Start one archive run for ``file`` and return its suspended gate decision."""

    run = engine.start(_archive_workflow(), subject=file, actor=actor)
    advance_once(run)
    execute_started(run)
    advance_once(run)
    execute_started(run)
    with system_context(reason="test workflows integrate decision"):
        return Decision.objects.select_related("step_run", "step_run__run").get(step_run__run=run)


def _archive_storage(tmp_path: Path, *, operator: Any, content: bytes) -> tuple[Any, Any]:
    """Create one READY storage file plus its operator-owned drive."""

    with system_context(reason="test workflows integrate storage"):
        backend = Backend._base_manager.create(
            slug=f"local-{operator.pk}",
            label="Local",
            backend_class="local",
            backend_config={"root": str(tmp_path), "base_url": "/media/"},
        )
        drive = Drive._base_manager.create(
            backend=backend,
            slug=f"assets-{operator.pk}",
            name="Assets",
            prefix=f"assets-{operator.pk}",
            created_by=operator,
        )
        file = File.objects.ingest_bytes(
            content,
            filename="archive.bin",
            owner_id=operator.pk,
            drive_id=str(drive.sqid),
        )
    return file, drive


def _archive_workflow() -> Any:
    """Return the probe -> gate -> prepare -> stock map -> unit workflow."""

    return workflow_with_steps(
        name="Archive import",
        steps=(
            {"key": "probe", "step_class": "archive_probe", "config": {}},
            {"key": "gate", "step_class": "archive_gate", "config": {}},
            {
                "key": "prepare",
                "step_class": "archive_execute",
                "config": {"mode": "prepare"},
            },
            {
                "key": "map",
                "step_class": "map",
                "config": {"target_step": "execute_unit", "items": "input"},
            },
            {
                "key": "execute_unit",
                "step_class": "archive_execute",
                "config": {"mode": "unit"},
            },
        ),
        edges=(
            ("probe", "gate", "recognized"),
            ("gate", "prepare", "completed"),
            ("prepare", "map", "prepared"),
        ),
    )
