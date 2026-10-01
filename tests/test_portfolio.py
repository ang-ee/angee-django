"""Portfolio rows use authenticated read scopes and retain ancestry invariants."""

from __future__ import annotations

import pytest
from django.contrib.auth.models import AnonymousUser
from django.core.exceptions import ValidationError
from django.db import models, transaction
from rebac import SubjectRef, actor_context, system_context
from rebac.models import active_relationship_model

from angee.base.models import AngeeDataModel
from angee.portfolio.models import InitiativeProject, PortfolioRole, Product
from tests.hierdemo.models import HierNode
from tests.projects_models import Project  # noqa: F401 -- register the product origin target


class ProductRow(Product):
    """Concrete adoption of the real portfolio product declaration."""

    class Meta(Product.Meta):
        abstract = False
        app_label = "portfolio"
        rebac_resource_type = "portfolio/product"


class PortfolioRoleRow(PortfolioRole):
    """Native role anchor needed by actor-scoped portfolio reads."""

    class Meta(PortfolioRole.Meta):
        abstract = False
        managed = False
        app_label = "portfolio"
        rebac_resource_type = "portfolio/role"


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


def test_portfolio_authenticated_reads_do_not_grant_write(composed_tables: None) -> None:
    """Real portfolio products are readable by signed-in actors without tuples."""

    del composed_tables
    reader = SubjectRef.of("auth/user", "1")
    with system_context(reason="test.portfolio.product"):
        row = ProductRow.objects.create(name="Shared")
        row.save()
        assert not active_relationship_model().objects.filter(
            resource_type="portfolio/product", resource_id=str(row.pk), relation="reader",
        ).exists()
    with actor_context(reader):
        readable = ProductRow.objects.get(pk=row.pk)
        assert readable.name == "Shared"
        assert not readable.has_access("write")
    with actor_context(AnonymousUser()):
        assert not ProductRow.objects.filter(pk=row.pk).exists()
