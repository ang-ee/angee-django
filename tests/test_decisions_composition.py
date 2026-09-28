"""Decision composition, settings registration, and scheduled expiry boundaries."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from django.apps import apps
from django.core.exceptions import ImproperlyConfigured
from graphql import build_schema

from angee.base.impl import resolve_impl_class
from angee.decisions.policies import DecisionPolicy
from angee.decisions.tasks import expire
from angee.jobs import locks


def test_decisions_compose_independently_with_inbox_read_resources(tmp_path: Path) -> None:
    """Compile only the decisions dependency closure in a fresh Django process."""
    root = Path(__file__).resolve().parents[1]
    environment = os.environ.copy()
    environment.pop("DJANGO_SETTINGS_MODULE", None)
    environment.pop("DATABASE_URL", None)
    composed = {}
    for action in ("snapshot", "schemas"):
        report = tmp_path / f"{action}.json"
        result = subprocess.run(
            [sys.executable, str(root / "tests/composed_host.py"), "--source-root", str(root),
             "--runtime-dir", str(tmp_path / action / "runtime"), "--app", "angee.decisions",
             "--no-examples", "--action", action, "--output", str(report)],
            cwd=root, env=environment, capture_output=True, text=True, timeout=120, check=False,
        )
        assert result.returncode == 0, f"Decision composition failed:\n{result.stdout}\n{result.stderr}"
        composed[action] = json.loads(report.read_text())
    assert not any(name.startswith("workflows.") for name in composed["snapshot"])
    assert {name for name in composed["snapshot"] if name.startswith("decisions.")} == {
        "decisions.decision", "decisions.decisiongroup", "decisions.decisionevidence",
    }
    assert all(not model["checks"] for model in composed["snapshot"].values())
    schema = build_schema(composed["schemas"]["console"])
    assert schema.query_type is not None
    assert {"decisions", "decisions_by_pk", "decision_groups", "decision_evidence"} <= schema.query_type.fields.keys()
    assert schema.mutation_type is not None
    mutations = schema.mutation_type.fields
    assert "decide" in mutations
    assert set(mutations["decide"].args) == {"id", "revision", "action", "values"}
    assert not any(name.startswith(("insert_decision", "update_decision", "delete_decision")) for name in mutations)
    filters = schema.query_type.fields["decisions"].args["where"].type
    assert {"assignees", "requester"} <= filters.fields.keys()


def test_expiry_task_uses_the_shared_lock_and_dispatches_one_manager_verb(monkeypatch) -> None:
    """Expiry dispatches through its manager only while holding the shared task lock."""
    backend = locks.LocalLockBackend()
    monkeypatch.setattr(locks, "get_lock_backend", lambda: backend)
    manager = apps.get_model("decisions", "Decision").objects
    calls = []
    key = locks.LockKey("decisions", ("expire",))

    def expire_due() -> int:
        assert backend.is_held(key)
        calls.append("expire")
        return 7

    monkeypatch.setattr(manager, "expire_due", expire_due)
    with locks.task_lock(key) as acquired:
        assert acquired
        assert expire.run() == 0
    assert calls == []
    assert expire.run() == 7
    assert calls == ["expire"]
    assert not backend.is_held(key)


class ExplicitPolicy(DecisionPolicy):
    """A neutral extension resolved through the configured registry."""

    key = "explicit"
    label = "Explicit"

    @classmethod
    def settled(cls, decisions: list[Any]) -> bool:
        """Settle exactly two answered or closed seats."""
        return len(decisions) == 2 and all(not decision.is_open for decision in decisions)


def test_policy_registry_can_be_extended_through_settings(settings) -> None:
    """Policy fields resolve explicit addon registrations and reject unknown keys."""
    settings.ANGEE_DECISION_POLICY_CLASSES = {
        **settings.ANGEE_DECISION_POLICY_CLASSES,
        "explicit": f"{__name__}.ExplicitPolicy",
    }
    group = apps.get_model("decisions", "DecisionGroup")
    field = group._meta.get_field("policy")
    assert field.resolve_class("explicit") is ExplicitPolicy
    assert resolve_impl_class("ANGEE_DECISION_POLICY_CLASSES", "explicit", DecisionPolicy) is ExplicitPolicy
    assert ExplicitPolicy.settled([SimpleNamespace(is_open=False), SimpleNamespace(is_open=False)])
    assert not ExplicitPolicy.settled([SimpleNamespace(is_open=False)])
    with pytest.raises(ImproperlyConfigured):
        field.resolve_class("unknown")
