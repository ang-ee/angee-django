"""Tests for declared catalogue/reference-data model traits."""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

import pytest
from django.db import connection, models
from django.test.utils import isolate_apps

from angee.addons import AddonContract
from angee.base.models import AngeeModel
from angee.resources.exceptions import ResourceLoadError
from angee.resources.models import Resource


@dataclass(slots=True)
class _Addon:
    """Small addon stand-in for resource-entry tests."""

    name: str
    """Full dotted addon name."""

    label: str
    """Short Django app label."""

    path: str
    """Filesystem root for local resource files."""

    _addon_contract: AddonContract
    """In-memory addon contract used by the resources manifest owner."""


def _addon(
    tmp_path: Path,
    *,
    manifest: dict[str, tuple[dict[str, Any], ...]],
) -> _Addon:
    """Return a resource addon rooted at ``tmp_path``."""

    name = "tests.catalogue_addon"
    return _Addon(
        name=name,
        label="catalogue_addon",
        path=str(tmp_path),
        _addon_contract=AddonContract(
            name=name,
            resources=manifest,
        ),
    )


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

        assert DeclaredCatalogue.is_catalogue_model() is True
        assert DeclaredCatalogue.get_catalogue_tier() == "master"
        assert ParentCatalogue.is_catalogue_model() is True
        assert ParentCatalogue.get_catalogue_tier() == "demo"
        assert ChildCatalogue.is_catalogue_model() is False
        assert ChildCatalogue.get_catalogue_tier() == "master"
        assert InstallCatalogue.is_catalogue_model() is True
        assert InstallCatalogue.get_catalogue_tier() == "install"


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

    models_to_create: tuple[type[models.Model], ...] = (
        CatalogueLoadThing,
        CatalogueLoadLedger,
    )
    with connection.schema_editor() as schema_editor:
        for model in models_to_create:
            schema_editor.create_model(model)
    try:
        with pytest.raises(ResourceLoadError, match="catalogue tier mismatch"):
            CatalogueLoadLedger.objects.load_addons(
                (owner,),
                tiers=[Resource.Tier.MASTER],
            )
    finally:
        with connection.schema_editor() as schema_editor:
            for model in reversed(models_to_create):
                schema_editor.delete_model(model)
