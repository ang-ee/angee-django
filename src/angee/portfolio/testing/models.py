"""Canonical concrete portfolio models for the bare-Django test runtime.

Permission queries resolve the portfolio backings (release products,
initiative placements), so register the models from conftest before Django
creates the test database.
"""

from django.db import models

from angee.portfolio.models import Initiative as AbstractInitiative
from angee.portfolio.models import InitiativeProject as AbstractInitiativeProject
from angee.portfolio.models import PortfolioRole as AbstractPortfolioRole
from angee.portfolio.models import Product as AbstractProduct
from angee.portfolio.models import Release as AbstractRelease
from angee.portfolio.models import Update as AbstractUpdate
from angee.projects.testing import (
    models as projects_models,  # noqa: F401 -- register the product origin and placement targets
)


class Product(AbstractProduct):
    """Concrete adoption of the real portfolio product declaration."""

    class Meta(AbstractProduct.Meta):
        abstract = False
        app_label = "portfolio"
        rebac_resource_type = "portfolio/product"



class PortfolioRole(AbstractPortfolioRole):
    """Native role anchor needed by actor-scoped portfolio reads."""

    class Meta(AbstractPortfolioRole.Meta):
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



class Release(AbstractRelease):
    product = models.ForeignKey(Product, on_delete=models.CASCADE, related_name="+")

    class Meta(AbstractRelease.Meta):
        abstract = False
        app_label = "portfolio"
        rebac_resource_type = "portfolio/release"
