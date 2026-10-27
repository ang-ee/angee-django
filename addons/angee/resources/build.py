"""Resources-owned projection of source-local catalogue declarations."""

from __future__ import annotations

from typing import Any

from django.apps import apps
from django.core.exceptions import ValidationError
from django.db import DatabaseError, models

from angee.compose.declarations import BuildIntegration, DeclarationValue, ModelDeclaration
from angee.compose.model_composition import ModelComposition
from angee.tiers import ResourceTier


class CatalogueIntegration(BuildIntegration):
    """Preserve explicit catalogue declarations when a source is materialized."""

    def model_declaration(self, composition: ModelComposition, source: type[models.Model]) -> ModelDeclaration:
        """Project only this source's declaration, never its parent's catalogue role."""

        if not source.__dict__.get("catalogue", False):
            return ModelDeclaration()
        attributes: dict[str, DeclarationValue] = {
            "catalogue": True,
            "catalogue_tier": str(source.__dict__.get("catalogue_tier", ResourceTier.MASTER)),
        }
        tiers = source.__dict__.get("catalogue_tiers")
        if tiers is not None:
            attributes["catalogue_tiers"] = (
                tuple(str(tier) if isinstance(tier, str) else tier for tier in tiers)
                if isinstance(tiers, tuple) else tiers
            )
        return ModelDeclaration(attributes=attributes)

    def retired_primary_keys(self, model: type[models.Model], alias: str) -> set[Any]:
        """Return only the existing resource-ledger retirement exemption.

        Missing ledgers, failed ledger reads, and invalid keys never authorize
        dropping a populated table. The ledger owns retiring-addon validation.
        """

        try:
            ledger = apps.get_model("resources", "Resource")
        except LookupError:
            return set()
        try:
            candidates = ledger.objects.using(alias).retired_seed_keys(model._meta.label)
        except DatabaseError:
            return set()
        keys = set()
        for candidate in candidates:
            try:
                keys.add(model._meta.pk.to_python(candidate))
            except ValidationError:
                continue
        return keys
