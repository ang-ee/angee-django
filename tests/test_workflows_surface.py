"""Review changes to the workflows, decisions, and extraction Python surface."""

from __future__ import annotations

import importlib
import importlib.util
import inspect
import pkgutil
from dataclasses import fields
from typing import get_args

from django.apps import apps
from django.db import models
from django.db.models.deletion import PROTECT
from pydantic import BaseModel

import angee.workflows as workflows
from angee.base.evidence import DerivedFrom, EvidenceFact, EvidenceReference, FactAuthority, readable_records
from angee.decisions.contracts import DecisionFact, DecisionRecordReference, DecisionRequest
from angee.workflows.managers import PublishResult
from angee.workflows.states import RunOrigin
from angee.workflows.steps import Settlement, Step
from angee.workflows.testing import drivers as test_drivers
from angee.workflows.triggers import RecordChanged, RecordChangedOptIn, TriggerGrantTarget, TriggerSource

EXPECTED_MODELS = {
    "workflows": (
        "StepArtifact StepAttempt StepRun StepWatch Trigger TriggerEvent Workflow WorkflowRun "
        "WorkflowRunEvidence WorkflowVersion"
    ),
    "decisions": "Decision DecisionEvidence DecisionGroup",
    "extraction": "Extraction ExtractionLineage ExtractionPage ExtractionPart ExtractionSource",
}

EXPECTED_VERBS = {
    "extraction.Extraction.manager": (
        "authorized_document_sources identity_preserving_pipeline_successor inference_authority_base "
        "inference_candidate_selectors inference_current_head latest_succeeded_identity_authority "
        "prepare_correction_binding prepare_pages retain_result reused_inference "
        "reviewed_correction_authority revise_from_decision"
    ),
    "extraction.Extraction.queryset": "validate_insert",
    "extraction.ExtractionLineage.queryset": "validate_insert",
    "extraction.ExtractionPage.queryset": "validate_insert",
    "extraction.ExtractionPart.queryset": "validate_insert",
    "extraction.ExtractionSource.queryset": "validate_insert",
    "workflows.StepAttempt.queryset": "close",
    "workflows.StepRun.manager": "record_await retry_step",
    "workflows.StepRun.queryset": (
        "cancel_open changed_records claim collect_map count_redispatch dispatch due expire expired "
        "extend_deadline fenced for_map settle settled_decisions terminal_runs to_ready to_running "
        "to_waiting undispatched"
    ),
    "workflows.StepWatch.manager": "record_change register wait_kind",
    "workflows.Trigger.manager": "admit disable drain enable enable_preview lock_grants reconcile_grants revoke_grant",
    "workflows.Trigger.queryset": "bulk_create update",
    "workflows.TriggerEvent.manager": "record_change",
    "workflows.TriggerEvent.queryset": "pending",
    "workflows.Workflow.manager": "authoring_outcomes install_definition publish save_draft save_identity",
    "workflows.WorkflowRun.manager": "cancel cancel_abandoned cancel_on_commit prune reopen reprocess start",
    "workflows.WorkflowRun.queryset": "for_subject hold hold_owned retention_candidates",
    "decisions.Decision.manager": "admit_group cancel_group decide expire_due reask resolution resolutions",
    "decisions.Decision.queryset": (
        "close due hold open open_expression pending reject_attempt resolve unanswered with_open_state"
    ),
    "decisions.DecisionEvidence.queryset": "for_records protect_record",
    "decisions.DecisionGroup.queryset": "hold settle settled",
}

EXPECTED_TYPES = {
    "awaits": "AwaitRun AwaitRunConfig AwaitRunInput",
    "context": "StepContext",
    "maps": "Map MapInput MapItem",
    "reviews": "Review ReviewConfig ReviewSeat ReviewStep",
    "steps": "Ask Done EmptyOutput Fail NextPage RetryPolicy Retryable Step StepMode Superseded Wait",
}

EXPECTED_RUNNER = tuple(
    f"Runner.{name}" for name in (
        "advance", "artifact", "begin_effect", "execute", "heartbeat", "raise_if_canceled", "reap",
        "redispatch", "tick", "wake", "wake_decisions", "wake_records", "wake_runs",
    )
)

EXPECTED_SETTINGS = {
    "intake": "ANGEE_DECISION_ACTION_CLASSES",
    "decisions": (
        "ANGEE_DECISION_ACTION_CLASSES ANGEE_DECISION_MAX_ATTEMPTS ANGEE_DECISION_POLICY_CLASSES "
        "ANGEE_IMPL_REGISTRIES:append"
    ),
    "extraction": "ANGEE_EXTRACTION_MAX_BYTES ANGEE_EXTRACTION_PROFILE_CLASSES ANGEE_IMPL_REGISTRIES:append",
    "workflows": (
        "ANGEE_IMPL_REGISTRIES:append ANGEE_WORKFLOW_MAP_CONCURRENCY ANGEE_WORKFLOW_MAX_DISPATCHES "
        "ANGEE_WORKFLOW_RETENTION_DAYS ANGEE_WORKFLOW_STEP_CLASSES ANGEE_WORKFLOW_TRIGGER_SOURCE_CLASSES"
    ),
    "workflows_integrate": "ANGEE_IMPL_REGISTRIES:append ANGEE_WORKFLOW_ARCHIVE_EXTRACTOR_CLASSES",
}

EXPECTED_TEST_DRIVERS = (
    "RunFactory", "capture_tasks", "decide", "load_workflow", "observe", "publish_draft", "register_steps",
    "run_until", "start_run", "trigger_source",
)


def _public_verbs(cls: type, label: str, base: type) -> tuple[str, ...]:
    """Count only addon-owned methods, including those behind generated managers."""
    return tuple(sorted({
        name
        for owner in cls.__mro__
        if owner.__module__.startswith(f"angee.{label}.") and issubclass(owner, base)
        for name, member in vars(owner).items()
        if not name.startswith("_") and (callable(member) or isinstance(member, (classmethod, staticmethod)))
    }))


def test_workflows_public_surface() -> None:
    """Every new or removed model, verb, type, or setting needs an inventory edit."""
    registered = {
        label: sorted(
            (model for model in apps.get_models() if model._meta.app_label == label),
            key=lambda model: model.__name__,
        )
        for label in EXPECTED_MODELS
    }
    assert {label: tuple(model.__name__ for model in models_) for label, models_ in registered.items()} == {
        label: tuple(names.split()) for label, names in EXPECTED_MODELS.items()
    }

    verbs = {}
    for label, models_ in registered.items():
        for model in models_:
            manager = model._default_manager
            for owner, cls, base in (
                ("manager", type(manager), models.Manager),
                ("queryset", manager._queryset_class, models.QuerySet),
            ):
                if names := _public_verbs(cls, label, base):
                    verbs[f"{label}.{model.__name__}.{owner}"] = names
    assert verbs == {owner: tuple(names.split()) for owner, names in EXPECTED_VERBS.items()}

    types = {}
    runner = []
    for module_info in pkgutil.iter_modules(workflows.__path__):
        if module_info.ispkg:
            continue
        module = importlib.import_module(f"angee.workflows.{module_info.name}")
        public_types = tuple(sorted(
            name for name, member in vars(module).items()
            if name.isidentifier() and not name.startswith("_")
            and inspect.isclass(member) and member.__module__ == module.__name__
            and (module_info.name in EXPECTED_TYPES or issubclass(member, (Step, *get_args(Settlement.__value__))))
        ))
        if public_types:
            types[module_info.name] = public_types
        if module_info.name == "runner":
            runner = sorted(
                name for name, member in vars(module).items()
                if not name.startswith("_") and inspect.isfunction(member) and member.__module__ == module.__name__
            )
            runner.extend(
                f"{name}.{verb}"
                for name, cls in vars(module).items()
                if not name.startswith("_") and inspect.isclass(cls) and cls.__module__ == module.__name__
                for verb in _public_verbs(cls, "workflows", object)
            )
    assert types == {module: tuple(names.split()) for module, names in EXPECTED_TYPES.items()}
    assert tuple(member.__name__ for member in get_args(Settlement.__value__)) == (
        "Done", "Wait", "NextPage", "Fail", "Ask",
    )
    assert all(
        {"check", "admit", "transition"} <= set(dir(member))
        and "kind" not in member.__dataclass_fields__
        for member in get_args(Settlement.__value__)
    )
    assert tuple(sorted(runner)) == EXPECTED_RUNNER

    settings = {}
    for config in apps.get_app_configs():
        if not config.name.startswith("angee."):
            continue
        module_name = f"{config.name}.autoconfig"
        if importlib.util.find_spec(module_name) is None:
            continue
        declared = importlib.import_module(module_name).SETTINGS
        names = tuple(sorted(
            key for key in declared
            if (key.startswith(("ANGEE_WORKFLOW_", "ANGEE_DECISION_", "ANGEE_EXTRACTION_")) and "." not in key)
            or (config.label in EXPECTED_SETTINGS and key == "ANGEE_IMPL_REGISTRIES:append")
        ))
        if names:
            settings[config.label] = names
    assert settings == {label: tuple(names.split()) for label, names in EXPECTED_SETTINGS.items()}


def test_workflow_test_driver_surface() -> None:
    """Changes to the public test harness require an inventory edit."""
    public = tuple(sorted(
        name for name, member in vars(test_drivers).items()
        if not name.startswith("_") and getattr(member, "__module__", None) == test_drivers.__name__
        and (inspect.isfunction(member) or inspect.isclass(member))
    ))
    assert public == EXPECTED_TEST_DRIVERS


def test_workflow_publication_result_surface() -> None:
    """Publication reports a version and explicit republish targets."""
    assert tuple(field.name for field in fields(PublishResult)) == ("version", "dependents")


def test_run_origin_and_trigger_event_surface() -> None:
    """Admission has one stored origin and one protected link to its sole cause."""
    run = apps.get_model("workflows", "WorkflowRun")
    event = apps.get_model("workflows", "TriggerEvent")
    assert run._meta.get_field("origin").choices_enum is RunOrigin
    assert not isinstance(run._meta.get_field("origin"), models.GeneratedField)
    assert tuple(RunOrigin.values) == ("manual", "workflow", "reprocess", "trigger")
    for name in ("parent_step", "reprocess_of", "trigger_event"):
        assert run._meta.get_field(name).remote_field.on_delete is PROTECT
    assert run._meta.get_field("trigger_event").one_to_one
    assert "run" not in {field.name for field in event._meta.get_fields()}
    assert event._meta.get_field("trigger").remote_field.on_delete is PROTECT
    assert {constraint.name for constraint in run._meta.constraints} >= {"workflows_run_origin_cause"}
    assert {constraint.name for constraint in event._meta.constraints} >= {"workflows_event_admission_evaluated"}
    assert {index.name for index in event._meta.indexes} >= {"workflows_event_pending"}


def test_trigger_principal_and_source_grant_surface() -> None:
    """A workflow owns a user principal and sources declare its grant scope."""
    workflow = apps.get_model("workflows", "Workflow")
    trigger = apps.get_model("workflows", "Trigger")
    assert workflow._meta.get_field("user").one_to_one
    assert workflow._meta.get_field("user").remote_field.on_delete is PROTECT
    assert isinstance(trigger._meta.get_field("granted_targets"), models.JSONField)
    assert issubclass(RecordChanged, TriggerSource)
    assert callable(TriggerSource.grant_targets)
    assert callable(RecordChangedOptIn.record_changed_grant_targets)
    assert tuple(TriggerGrantTarget.__dataclass_fields__) == (
        "resource", "relation", "grant_permission", "grant_resource",
    )


def test_evidence_owner_surface() -> None:
    """Decision, run and extraction evidence compose the base derivation and admission owners."""
    evidence = apps.get_model("decisions", "DecisionEvidence")
    run_evidence = apps.get_model("workflows", "WorkflowRunEvidence")
    extraction_source = apps.get_model("extraction", "ExtractionSource")
    extraction = apps.get_model("extraction", "Extraction")
    decision = apps.get_model("decisions", "Decision")
    assert issubclass(evidence, DerivedFrom)
    assert issubclass(run_evidence, DerivedFrom)
    assert issubclass(extraction_source, DerivedFrom)
    assert callable(extraction.fact_correction) and callable(extraction.fact_authority)
    assert issubclass(DecisionFact, EvidenceFact)
    assert issubclass(DecisionRecordReference, EvidenceReference)
    assert issubclass(DecisionRequest, BaseModel)
    assert tuple(FactAuthority) == ("source", "correction", "unverified")
    assert callable(readable_records)
    assert next(index.fields for index in decision._meta.indexes if index.name == "decisions_subject") == [
        "subject_content_type", "subject_object_id", "kind",
    ]
