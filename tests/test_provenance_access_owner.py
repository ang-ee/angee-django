"""Plain protected Changesets and Angee rows share the same actual access gate."""

from typing import Any

import pytest
from django.apps import apps
from django.contrib.contenttypes.models import ContentType
from django.core.exceptions import ImproperlyConfigured
from rebac import PermissionDenied, actor_context, system_context, to_subject_ref

from angee.iam.models.base import ProtectedModel
from angee.iam.permissions import require_record_access
from angee.projects.testing.models import Project
from angee.provenance.context import changeset, changeset_model_label, current_changeset
from angee.provenance.contrib.angee.policy import angee_policy
from angee.provenance.engine.policy import Policy
from tests.conftest import create_user


@pytest.fixture(params=("project", "changeset"))
def access_row(request: Any, composed_tables: None) -> tuple[Any, Any, Any]:
    owner = create_user(f"shared-gate-owner-{request.param}")
    outsider = create_user(f"shared-gate-outsider-{request.param}")
    with actor_context(owner), changeset(fresh=True):
        project = Project.objects.create(title="Shared actor admission")
        receipt = current_changeset(required=True, record=True)
    model, pk = (
        (Project, project.pk) if request.param == "project" else (apps.get_model(changeset_model_label()), receipt.pk)
    )
    return model._base_manager.get(pk=pk), owner, outsider


def test_explicit_and_pinned_requester_parity(access_row: tuple[Any, Any, Any]) -> None:
    row, owner, outsider = access_row
    with actor_context(outsider):
        assert require_record_access(row, "write", owner) is owner
        assert row.actor() == to_subject_ref(owner)
        assert require_record_access(row, "write") == to_subject_ref(owner)
        if isinstance(row, ProtectedModel):
            assert row.require_access("write") == to_subject_ref(owner)
        angee_policy().authorize(None, "change", row)


def test_requester_clears_instance_and_ambient_sudo(access_row: tuple[Any, Any, Any]) -> None:
    row, owner, outsider = access_row
    row.with_actor(owner).sudo(reason="test.shared-gate.instance")
    with actor_context(owner), system_context(reason="test.shared-gate.ambient"):
        with pytest.raises(PermissionDenied, match="'write'"):
            require_record_access(row, "write", outsider)
        assert row.actor() == to_subject_ref(outsider)
        assert not row.is_sudo()
        assert require_record_access(row, "write", owner) is owner


@pytest.mark.parametrize("strict", (True, False))
def test_missing_requester_refuses_but_explicit_system_scope_survives(
    access_row: tuple[Any, Any, Any],
    settings: Any,
    strict: bool,
) -> None:
    row, _owner, outsider = access_row
    settings.REBAC_STRICT_MODE = strict
    with pytest.raises(PermissionDenied, match="requires an actor"):
        require_record_access(row, "write")
    with system_context(reason="test.shared-gate.explicit-system"):
        assert require_record_access(row, "write") is None
    row.sudo(reason="test.shared-gate.instance")
    with actor_context(outsider), pytest.raises(PermissionDenied, match="'write'"):
        require_record_access(row, "write")


@pytest.mark.django_db
def test_protected_policy_rejects_unconfigured_models() -> None:
    with system_context(reason="test.shared-gate.unconfigured"), pytest.raises(ImproperlyConfigured):
        angee_policy().authorize(None, "change", ContentType())


def test_protected_policy_does_not_accept_a_neutral_policy(settings: Any) -> None:
    settings.PROVENANCE_POLICY = Policy
    with pytest.raises(ImproperlyConfigured, match="extend AngeePolicy"):
        angee_policy()
