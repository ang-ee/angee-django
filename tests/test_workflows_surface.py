"""Review changes to the workflows and decisions public Python surface."""

from __future__ import annotations

import importlib
import importlib.util
import inspect
import pkgutil

from django.apps import apps
from django.db import models

import angee.workflows as workflows
from angee.workflows.steps import Settlement, Step
from angee.workflows.testing import drivers as test_drivers

EXPECTED_MODELS = {
    "workflows": "StepArtifact StepAttempt StepRun StepWatch Trigger TriggerEvent Workflow WorkflowRun WorkflowVersion",
    "decisions": "Decision DecisionEvidence DecisionGroup",
}

EXPECTED_VERBS = {
    "workflows.StepAttempt.queryset": "close",
    "workflows.StepRun.manager": (
        "artifact begin_effect execute heartbeat raise_if_canceled reap record_await redispatch resolution "
        "retry_step tick wake wake_decisions wake_records wake_runs"
    ),
    "workflows.StepRun.queryset": (
        "cancel_open changed_records claim collect_map count_redispatch dispatch due expire expired "
        "extend_deadline fenced for_map settle settled_decisions terminal_runs to_ready to_running "
        "to_waiting undispatched"
    ),
    "workflows.StepWatch.manager": "record_change register wait_kind",
    "workflows.Trigger.manager": "admit disable drain enable",
    "workflows.Trigger.queryset": "bulk_create update",
    "workflows.TriggerEvent.manager": "record_change",
    "workflows.TriggerEvent.queryset": "pending",
    "workflows.Workflow.manager": "install_definition publish save_draft save_identity",
    "workflows.WorkflowRun.manager": "advance cancel cancel_abandoned cancel_on_commit prune reopen reprocess start",
    "workflows.WorkflowRun.queryset": "for_subject hold hold_owned retention_candidates",
    "decisions.Decision.manager": "admit_group cancel_group decide expire_due reask resolution resolutions",
    "decisions.Decision.queryset": (
        "close due hold open open_expression pending reject_attempt resolve unanswered with_open_state"
    ),
    "decisions.DecisionEvidence.queryset": "for_records protect_record",
    "decisions.DecisionGroup.queryset": "hold settle settled",
}

EXPECTED_TYPES = {
    "awaits": "AwaitRun AwaitRunConfig AwaitRunInput AwaitedRun",
    "context": "StepContext",
    "maps": "Map MapInput MapItem MapWait",
    "reviews": "Ask Review ReviewConfig ReviewSeat ReviewStep",
    "steps": "Done EmptyOutput Fail NextPage RetryPolicy Retryable Settlement Step Superseded Wait",
}

EXPECTED_RUNNER = ()

EXPECTED_SETTINGS = {
    "decisions": "ANGEE_DECISION_ACTION_CLASSES ANGEE_DECISION_MAX_ATTEMPTS ANGEE_DECISION_POLICY_CLASSES",
    "workflows": (
        "ANGEE_WORKFLOW_MAP_CONCURRENCY ANGEE_WORKFLOW_MAX_DISPATCHES ANGEE_WORKFLOW_RETENTION_DAYS "
        "ANGEE_WORKFLOW_STEP_CLASSES ANGEE_WORKFLOW_TRIGGER_SOURCES"
    ),
    "workflows_integrate": "ANGEE_WORKFLOW_ARCHIVE_EXTRACTOR_CLASSES",
}

EXPECTED_TEST_DRIVERS = (
    "RunFactory", "capture_tasks", "decide", "load_workflow", "observe", "register_steps", "run_until",
    "start_run", "trigger_source",
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
            and (module_info.name in EXPECTED_TYPES or issubclass(member, (Step, Settlement)))
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
            if key.startswith(("ANGEE_WORKFLOW_", "ANGEE_DECISION_")) and "." not in key
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
