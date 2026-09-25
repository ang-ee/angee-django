"""Tests for declared catalogue/reference-data model traits."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from django.apps import AppConfig
from django.db import models
from django.test.utils import isolate_apps

from angee.addons import addon_manifest
from angee.base.models import AngeeModel
from angee.resources.exceptions import ResourceLoadError
from angee.resources.models import Resource
from tests.conftest import make_addon
from tests.tables import model_tables


def _addon(
    tmp_path: Path,
    *,
    manifest: dict[str, tuple[dict[str, Any], ...]],
) -> AppConfig:
    """Return a resource addon rooted at ``tmp_path``."""

    config = make_addon(
        name="tests.catalogue_addon", label="catalogue_addon", path=tmp_path, resources=dict(manifest or {})
    )
    addon_manifest(config)
    return config


def test_catalogue_marker_and_tier_are_declared_per_class() -> None:
    """A catalogue declaration belongs to the class that declares it, not children."""

    with isolate_apps():

        class DeclaredCatalogue(AngeeModel):
            """Concrete model that declares the catalogue trait."""

            catalogue = True
            name = models.CharField(max_length=40)

            class Meta:
                """Django model options for the test model."""

                app_label = "base"

        class ParentCatalogue(AngeeModel):
            """Parent whose catalogue declaration must not leak to subclasses."""

            catalogue = True
            catalogue_tier = "demo"
            name = models.CharField(max_length=40)

            class Meta:
                """Django model options for the test parent."""

                app_label = "base"

        class ChildCatalogue(ParentCatalogue):
            """Subclass that does not redeclare the catalogue trait."""

            extra = models.CharField(max_length=40, blank=True)

            class Meta:
                """Django model options for the test child."""

                app_label = "base"

        class InstallCatalogue(AngeeModel):
            """Concrete model that declares a non-default catalogue tier."""

            catalogue = True
            catalogue_tier = "install"
            name = models.CharField(max_length=40)

            class Meta:
                """Django model options for the install-tier test model."""

                app_label = "base"

        class MultiTierCatalogue(AngeeModel):
            """Catalogue with one default authoring tier and an additional load tier."""

            catalogue = True
            catalogue_tier = "demo"
            catalogue_tiers = ("install", "demo")
            name = models.CharField(max_length=40)

            class Meta:
                app_label = "base"

        assert DeclaredCatalogue.is_catalogue_model() is True
        assert DeclaredCatalogue.get_catalogue_tier() == "master"
        assert ParentCatalogue.is_catalogue_model() is True
        assert ParentCatalogue.get_catalogue_tier() == "demo"
        assert ChildCatalogue.is_catalogue_model() is False
        assert ChildCatalogue.get_catalogue_tier() == "master"
        assert InstallCatalogue.is_catalogue_model() is True
        assert InstallCatalogue.get_catalogue_tier() == "install"
        assert MultiTierCatalogue.get_catalogue_tier() == "demo"
        assert MultiTierCatalogue.get_catalogue_tiers() == ("install", "demo")
        assert not [error for error in MultiTierCatalogue.check() if error.id == "angee.E014"]


def test_invalid_catalogue_tier_is_a_system_check_error() -> None:
    """Invalid tiers report through Django checks instead of class construction."""

    with isolate_apps():

        class InvalidCatalogue(AngeeModel):
            """Concrete model with an invalid catalogue tier."""

            catalogue = True
            catalogue_tier = "broken"
            name = models.CharField(max_length=40)

            class Meta:
                """Django model options for the invalid-tier test model."""

                app_label = "base"

        errors = InvalidCatalogue.check()

    catalogue_errors = [error for error in errors if error.id == "angee.E014"]
    assert len(catalogue_errors) == 1
    assert "catalogue_tier" in catalogue_errors[0].msg


@pytest.mark.parametrize(
    ("default_tier", "allowed_tiers"),
    [
        ("broken", ("install", "demo")),
        ("install", ()),
        ("install", ("demo",)),
        ("install", ("install", "install")),
        ("install", ("install", 1)),
        ("install", ["install", "demo"]),
    ],
)
def test_invalid_multi_tier_catalogue_contract_is_a_system_check_error(
    default_tier: str, allowed_tiers: object,
) -> None:
    """A multi-tier catalogue keeps one valid included default authoring tier."""

    with isolate_apps():

        class InvalidMultiTierCatalogue(AngeeModel):
            catalogue = True
            catalogue_tier = default_tier
            catalogue_tiers = allowed_tiers
            name = models.CharField(max_length=40)

            class Meta:
                app_label = "base"

        errors = InvalidMultiTierCatalogue.check()

    assert [error.id for error in errors].count("angee.E014") == 1


@pytest.mark.django_db(transaction=True)
def test_resource_loader_rejects_catalogue_tier_mismatch(tmp_path: Path) -> None:
    """Catalogue resource manifests must use the tier declared by the target model."""

    class AbstractCatalogueLoadThing(AngeeModel):
        """Abstract catalogue source model used to exercise loader-tier validation."""

        catalogue = True
        catalogue_tier = "install"
        name = models.CharField(max_length=40)

        class Meta:
            """Django model options for the abstract loader test model."""

            abstract = True
            app_label = "base"

    class CatalogueLoadThing(AbstractCatalogueLoadThing):
        """Concrete emitted-shape model carrying catalogue markers in its own body."""

        catalogue = True
        catalogue_tier = "install"

        class Meta(AbstractCatalogueLoadThing.Meta):
            """Django model options for the concrete loader test model."""

            abstract = False
            app_label = "base"

    class CatalogueLoadLedger(Resource):
        """Concrete resource ledger for the loader test."""

        class Meta(Resource.Meta):
            """Django model options for the loader test ledger."""

            app_label = "base"
            abstract = False

    resource_dir = tmp_path / "resources"
    resource_dir.mkdir()
    (resource_dir / "010_base.catalogueloadthing.csv").write_text(
        "_xref,name\none,One\n",
        encoding="utf-8",
    )
    owner = _addon(
        tmp_path,
        manifest={
            "master": ({"path": "resources/010_base.catalogueloadthing.csv"},),
            "install": (),
            "demo": (),
        },
    )

    with model_tables((CatalogueLoadThing, CatalogueLoadLedger)):
        with pytest.raises(ResourceLoadError, match="catalogue tier mismatch"):
            CatalogueLoadLedger.objects.load_addons(
                (owner,),
                tiers=[Resource.Tier.MASTER],
            )
