"""Portfolio rows compose the shared-reader lifecycle and queryset guards."""

from __future__ import annotations

import pytest
from django.core.exceptions import ValidationError
from django.db import connection, models, transaction
from rebac import SubjectRef, actor_context, system_context
from rebac.backends import LocalBackend, backend, reset_backend
from rebac.models import active_relationship_model
from rebac.schema import parse_zed

from angee.base.models import AngeeDataModel
from angee.portfolio.models import Initiative, InitiativeProject, Product, Release, Update, WorkspaceVisibleMixin
from tests.conftest import _clear_model_tables, _create_missing_tables
from tests.hierdemo.models import HierNode


class WorkspaceRow(WorkspaceVisibleMixin, AngeeDataModel):
    """Minimal concrete consumer of portfolio's shared workspace posture."""

    name = models.CharField(max_length=100)

    class Meta:
        app_label = "portfolio"
        rebac_resource_type = "tests/workspace_row"


class InitiativePlacementRow(AngeeDataModel):
    """Minimal persisted placement exercising the portfolio ancestry invariant."""

    initiative = models.ForeignKey(HierNode, on_delete=models.CASCADE, related_name="+")
    project = models.ForeignKey(HierNode, on_delete=models.CASCADE, related_name="+")

    class Meta:
        app_label = "portfolio"


@pytest.mark.django_db(transaction=True)
@pytest.mark.parametrize("lock", [False, True])
def test_placement_validates_current_ancestry_after_cached_initiative_moves(lock: bool) -> None:
    """A cached old path cannot admit a placement beneath the same project's ancestor."""

    test_models = (HierNode, InitiativePlacementRow)
    created = _create_missing_tables(test_models)
    try:
        with system_context(reason="test.portfolio.ancestry"), transaction.atomic():
            ancestor = HierNode.objects.create(name="Ancestor")
            retained = HierNode.objects.create(name="Retained")
            project = HierNode.objects.create(name="Project")
            InitiativePlacementRow.objects.create(initiative=ancestor, project=project)
            candidate = InitiativePlacementRow(initiative=retained, project=project)
            moved = HierNode.objects.get(pk=retained.pk)
            moved.parent = ancestor
            moved.save()

            assert candidate.initiative.path == retained.path
            assert not retained.path.startswith(ancestor.path)
            assert moved.path.startswith(ancestor.path)
            with pytest.raises(ValidationError, match="ancestry path"):
                InitiativeProject._validate_ancestry(candidate, lock=lock)
    finally:
        _clear_model_tables(test_models)
        if created:
            with connection.schema_editor() as editor:
                for model in reversed(created):
                    editor.delete_model(model)


@pytest.mark.parametrize("model", [Product, Initiative, InitiativeProject, Update, Release])
def test_portfolio_bulk_creation_cannot_skip_readers(model: type[models.Model]) -> None:
    """Every portfolio manager preserves the shared reader's create guard."""

    with pytest.raises(ValidationError, match="native owner"):
        model._meta.default_manager.bulk_create([])


@pytest.mark.django_db(transaction=True)
def test_workspace_reader_is_proposed_and_reconciled_without_granting_write() -> None:
    """Workspace rows stay readable through ordinary REBAC-scoped queries."""

    reset_backend()
    active = backend()
    assert isinstance(active, LocalBackend)
    active.set_schema(
        parse_zed(
            "definition auth/user {}\n"
            "definition tests/workspace_row {\n"
            "  relation reader: auth/user:*\n"
            "  relation writer: auth/user\n"
            "  permission read = reader\n"
            "  permission write = writer\n"
            "}\n"
        )
    )
    created = _create_missing_tables((WorkspaceRow,))
    try:
        reader = SubjectRef.of("auth/user", "workspace-reader")
        with system_context(reason="test.portfolio.workspace"):
            row = WorkspaceRow.objects.create(name="Shared")
            assert row.proposed_relationships()["reader"]
            row.save()
            assert (
                active_relationship_model()
                .objects.filter(resource_type="tests/workspace_row", resource_id=str(row.pk), relation="reader")
                .count()
                == 1
            )
        with actor_context(reader):
            readable = WorkspaceRow.objects.get(pk=row.pk)
            assert readable.name == "Shared"
            assert not readable.has_access("write")
    finally:
        _clear_model_tables((WorkspaceRow,))
        if created:
            with connection.schema_editor() as editor:
                for model in reversed(created):
                    editor.delete_model(model)
        reset_backend()
