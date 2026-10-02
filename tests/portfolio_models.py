"""Canonical concrete portfolio models for the bare-Django test runtime.

Permission queries resolve the portfolio backings (release products,
initiative placements), so register the models from conftest before Django
creates the test database.
"""

from django.db import models

from angee.portfolio.models import Initiative as AbstractInitiative
from angee.portfolio.models import InitiativeProject as AbstractInitiativeProject
from angee.portfolio.models import PortfolioRole, Product
from angee.portfolio.models import Release as AbstractRelease
from angee.portfolio.models import Update as AbstractUpdate
from tests import projects_models  # noqa: F401 -- register the product origin and placement targets


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



class Initiative(AbstractInitiative):
    class Meta(AbstractInitiative.Meta):
        abstract = False
        app_label = "portfolio"
        rebac_resource_type = "portfolio/initiative"



class InitiativeProject(AbstractInitiativeProject):
    class Meta(AbstractInitiativeProject.Meta):
        abstract = False
        app_label = "portfolio"
        rebac_resource_type = "portfolio/initiative_project"



class Update(AbstractUpdate):
    class Meta(AbstractUpdate.Meta):
        abstract = False
        app_label = "portfolio"
        rebac_resource_type = "portfolio/update"



class ReferenceRelease(AbstractRelease):
    product = models.ForeignKey(ProductRow, on_delete=models.CASCADE, related_name="+")

    class Meta(AbstractRelease.Meta):
        abstract = False
        app_label = "portfolio"
        rebac_resource_type = "portfolio/release"
